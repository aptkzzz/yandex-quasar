"""SpotterTailTrimmer: cut the wake-word tail only when a pause follows it."""

from __future__ import annotations

import numpy as np

from quasar_adapter.audio import SpotterTailTrimmer

RATE = 16000


def tone(ms: int) -> bytes:
    t = np.arange(RATE * ms // 1000) / RATE
    return (np.sin(2 * np.pi * 300 * t) * 8000).astype("<i2").tobytes()


def silence(ms: int) -> bytes:
    return bytes(RATE * ms // 1000 * 2)


def run(audio: bytes, chunk_ms: int = 20) -> tuple[bytes, SpotterTailTrimmer]:
    trimmer = SpotterTailTrimmer(window_ms=300, threshold_db=-40)
    step = RATE * chunk_ms // 1000 * 2
    out = b"".join(trimmer.feed(audio[i:i + step]) for i in range(0, len(audio), step))
    return out, trimmer


def test_tail_followed_by_pause_is_cut():
    audio = tone(200) + silence(800) + tone(1000)
    out, trimmer = run(audio)
    # Cut at the end of the 100 ms gap that follows the tail.
    assert trimmer.cut_bytes == RATE * 300 // 1000 * 2
    assert out == audio[trimmer.cut_bytes:]


def test_command_right_after_wake_word_is_kept():
    audio = tone(1500) + silence(300)
    out, trimmer = run(audio)
    assert trimmer.cut_bytes == 0
    assert out == audio


def test_short_pause_inside_command_is_not_a_gap():
    audio = tone(150) + silence(60) + tone(1000)
    out, trimmer = run(audio)
    assert trimmer.cut_bytes == 0
    assert out == audio


def test_leading_silence_is_kept():
    audio = silence(500) + tone(1000)
    out, trimmer = run(audio)
    assert trimmer.cut_bytes == 0
    assert out == audio


def test_uneven_chunks():
    audio = tone(200) + silence(800) + tone(500)
    out, trimmer = run(audio, chunk_ms=7)
    assert out == audio[trimmer.cut_bytes:]
    assert trimmer.cut_bytes == RATE * 300 // 1000 * 2
