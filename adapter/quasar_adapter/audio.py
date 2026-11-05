"""Audio helpers: Ogg/Opus demux + decode for the microphone, Ogg/Opus encode for responses."""

from __future__ import annotations

import asyncio
import logging
import struct
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import av
import numpy as np

_LOGGER = logging.getLogger(__name__)

PCM_RATE = 16000
OGG_CAPTURE = b"OggS"
OPUS_GRANULE_RATE = 48000


@dataclass
class OggPacket:
    serial: int
    data: bytes
    bos: bool  # first packet of a new logical stream


class OggDemuxer:
    """Incremental Ogg demuxer: accepts arbitrary byte chunks, yields complete packets."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._partial: dict[int, bytearray] = {}
        self._fresh_bos: set[int] = set()

    def feed(self, data: bytes) -> Iterator[OggPacket]:
        self._buf += data
        while True:
            page = self._take_page()
            if page is None:
                return
            yield from self._packets(page)

    def _take_page(self) -> bytes | None:
        start = self._buf.find(OGG_CAPTURE)
        if start < 0:
            # Keep a possible partial capture pattern at the tail.
            del self._buf[:-3]
            return None
        if start:
            _LOGGER.debug("ogg: skipped %d junk bytes", start)
            del self._buf[:start]
        if len(self._buf) < 27:
            return None
        nseg = self._buf[26]
        if len(self._buf) < 27 + nseg:
            return None
        body_len = sum(self._buf[27:27 + nseg])
        total = 27 + nseg + body_len
        if len(self._buf) < total:
            return None
        page = bytes(self._buf[:total])
        del self._buf[:total]
        return page

    def _packets(self, page: bytes) -> Iterator[OggPacket]:
        header_type = page[5]
        serial = struct.unpack_from("<I", page, 14)[0]
        nseg = page[26]
        lacing = page[27:27 + nseg]
        pos = 27 + nseg
        if header_type & 0x02:  # BOS
            self._partial.pop(serial, None)
            self._fresh_bos.add(serial)
        elif not header_type & 0x01:  # not a continuation: drop stale partial data
            self._partial.pop(serial, None)
        acc = self._partial.setdefault(serial, bytearray())
        for size in lacing:
            acc += page[pos:pos + size]
            pos += size
            if size < 255:
                bos = serial in self._fresh_bos
                self._fresh_bos.discard(serial)
                yield OggPacket(serial=serial, data=bytes(acc), bos=bos)
                acc = bytearray()
        self._partial[serial] = acc


class MicDecoder:
    """Decodes station microphone Ogg/Opus into PCM s16le 16 kHz mono, as the station sent it (no gain)."""

    def __init__(self) -> None:
        self._demux = OggDemuxer()
        self._codec: av.CodecContext | None = None
        self._resampler = av.AudioResampler(format="s16", layout="mono", rate=PCM_RATE)
        self.logical_streams = 0

    def feed(self, data: bytes) -> bytes:
        out = bytearray()
        for packet in self._demux.feed(data):
            if packet.bos:
                self.logical_streams += 1
                self._codec = av.CodecContext.create("libopus", "r")
                self._codec.sample_rate = 48000
                self._codec.layout = "mono"
            if packet.data.startswith((b"OpusHead", b"OpusTags")) or self._codec is None:
                continue
            try:
                frames = self._codec.decode(av.Packet(packet.data))
            except av.FFmpegError as err:
                _LOGGER.debug("opus decode error: %s", err)
                continue
            for frame in frames:
                for res in self._resampler.resample(frame):
                    out += res.to_ndarray().reshape(-1).astype("<i2").tobytes()
        return bytes(out)


def apply_gain(pcm: bytes, gain_db: float) -> bytes:
    """Fixed gain for s16le PCM with hard clipping."""
    if not gain_db or not pcm:
        return pcm
    samples = np.frombuffer(pcm, "<i2").astype(np.float32) * float(10 ** (gain_db / 20))
    return np.clip(samples, -32768, 32767).astype("<i2").tobytes()


def pcm_level_db(pcm: bytes) -> float:
    """RMS level of s16le PCM in dBFS."""
    samples = np.frombuffer(pcm, "<i2").astype(np.float32)
    if not len(samples):
        return -120.0
    rms = float(np.sqrt(np.mean(samples * samples)))
    return 20 * np.log10(rms / 32768 + 1e-9)


class SpotterTailTrimmer:
    """Cuts the end of the wake word from the start of the command audio, but only when a pause follows it.

    Audio after SpotterEnd may begin with the tail of "Алиса" ("-са"), which STT turns into an extra word
    ("Всё,", "Так,"). A fixed cut also eats the start of a command spoken right after the wake word
    ("Выключи таймер" -> "И читаемых"), so the leading sound is cut only if it ends within `window_ms`
    and at least `gap_ms` of silence follows. The cut lands at the end of that silence.
    """

    FRAME_BYTES = PCM_RATE * 2 // 50  # 20 ms of s16le

    def __init__(self, window_ms: int, threshold_db: float, gap_ms: int = 100) -> None:
        self._window_frames = window_ms // 20
        self._gap_frames = max(1, gap_ms // 20)
        self._threshold_db = threshold_db
        self._buffer = bytearray()
        self._forward = bytearray()
        self._frames = 0
        self._loud_seen = False
        self._silent_run = 0
        self.cut_bytes: int | None = None  # decided cut; None while deciding

    def feed(self, pcm: bytes, forward: bytes | None = None) -> bytes:
        """Return the audio to forward: nothing while deciding, then everything after the cut.

        ``pcm`` is what the decision is made on (levels are absolute); ``forward`` is a same-length stream to cut
        and return instead, e.g. audio without gain when HA wants it raw. Defaults to ``pcm`` itself.
        """
        forward = pcm if forward is None else forward
        if self.cut_bytes is not None:
            return forward
        self._buffer += pcm
        self._forward += forward
        while self.cut_bytes is None and (self._frames + 1) * self.FRAME_BYTES <= len(self._buffer):
            start = self._frames * self.FRAME_BYTES
            loud = pcm_level_db(bytes(self._buffer[start:start + self.FRAME_BYTES])) > self._threshold_db
            self._frames += 1
            if loud:
                self._loud_seen, self._silent_run = True, 0
                if self._frames > self._window_frames:
                    self.cut_bytes = 0  # speech goes on past the window: it is the command itself
            else:
                self._silent_run += 1
                if self._silent_run >= self._gap_frames:
                    self.cut_bytes = self._frames * self.FRAME_BYTES if self._loud_seen else 0
        if self.cut_bytes is None:
            return b""
        out = bytes(self._forward[self.cut_bytes:])
        self._buffer, self._forward = bytearray(), bytearray()
        return out


def split_ogg_pages(data: bytes) -> tuple[list[bytes], bytes]:
    """Split a byte buffer into complete Ogg pages; return (pages, remainder)."""
    pages = []
    pos = 0
    while True:
        if len(data) - pos < 27:
            break
        if data[pos:pos + 4] != OGG_CAPTURE:
            nxt = data.find(OGG_CAPTURE, pos + 1)
            if nxt < 0:
                return pages, b""
            pos = nxt
            continue
        nseg = data[pos + 26]
        if len(data) - pos < 27 + nseg:
            break
        total = 27 + nseg + sum(data[pos + 27:pos + 27 + nseg])
        if len(data) - pos < total:
            break
        pages.append(data[pos:pos + total])
        pos += total
    return pages, data[pos:]


def page_granule(page: bytes) -> int:
    return struct.unpack_from("<q", page, 6)[0]


class OpusTranscoder:
    """Transcodes one or more audio URLs (played back to back) into a stream of Ogg/Opus 48 kHz chunks.

    Uses an ffmpeg subprocess so any input HA can produce (wav/mp3/flac/ogg) and any http(s) URL works.
    Chunks are groups of whole Ogg pages not larger than ``max_chunk``.
    """

    def __init__(self, urls: list[str], max_chunk: int = 32 * 1024, ffmpeg: str = "ffmpeg") -> None:
        self.urls = urls
        self.max_chunk = max_chunk
        self.ffmpeg = ffmpeg
        self.duration = 0.0
        self._proc: asyncio.subprocess.Process | None = None

    def _args(self) -> list[str]:
        args = [self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error"]
        for url in self.urls:
            args += ["-i", url]
        if len(self.urls) > 1:
            norm = "".join(
                f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=mono[a{i}];"
                for i in range(len(self.urls))
            )
            inputs = "".join(f"[a{i}]" for i in range(len(self.urls)))
            args += ["-filter_complex", f"{norm}{inputs}concat=n={len(self.urls)}:v=0:a=1[out]",
                     "-map", "[out]"]
        args += [
            "-ac", "1", "-ar", "48000", "-c:a", "libopus", "-b:a", "48k", "-application", "voip",
            "-page_duration", "100000", "-flush_packets", "1", "-f", "ogg", "pipe:1",
        ]
        return args

    async def chunks(self) -> AsyncIterator[bytes]:
        self._proc = await asyncio.create_subprocess_exec(
            *self._args(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        assert self._proc.stdout is not None
        pending = b""
        batch = bytearray()
        last_granule = 0
        try:
            while True:
                data = await self._proc.stdout.read(16384)
                if not data:
                    break
                pages, pending = split_ogg_pages(pending + data)
                for page in pages:
                    last_granule = max(last_granule, page_granule(page))
                    if batch and len(batch) + len(page) > self.max_chunk:
                        yield bytes(batch)
                        batch = bytearray()
                    batch += page
                # Flush what we have: low latency beats fewer frames.
                if batch:
                    yield bytes(batch)
                    batch = bytearray()
            rc = await self._proc.wait()
            if rc != 0:
                err = (await self._proc.stderr.read()).decode(errors="replace") if self._proc.stderr else ""
                raise RuntimeError(f"ffmpeg exited with {rc}: {err.strip()[:500]}")
        finally:
            if self._proc.returncode is None:
                self._proc.kill()
                await self._proc.wait()
            self.duration = max(0.0, last_granule / OPUS_GRANULE_RATE)

    async def cancel(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.kill()
