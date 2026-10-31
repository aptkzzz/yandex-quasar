"""Media URL helpers."""

from __future__ import annotations

import logging

import aiohttp

_LOGGER = logging.getLogger(__name__)

MAX_REDIRECTS = 5


async def resolve_media_url(http: aiohttp.ClientSession, url: str) -> tuple[str, bool]:
    """Follow redirects ourselves (station's libsoup fails on 308) and detect endless streams.

    Returns (final_url, is_stream) where is_stream means "no Content-Length" (e.g. Music Assistant flow mode).
    On any network error the original URL is returned unchanged.
    """
    try:
        async with http.get(
            url, allow_redirects=True, max_redirects=MAX_REDIRECTS, headers={"Range": "bytes=0-0"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            final = str(resp.url)
            if resp.status == 206:
                is_stream = "/" not in resp.headers.get("Content-Range", "") or resp.headers.get(
                    "Content-Range", ""
                ).endswith("/*")
            else:
                is_stream = resp.content_length is None
    except (aiohttp.ClientError, TimeoutError) as err:
        _LOGGER.warning("Could not probe %s: %s", url, err)
        return url, False
    if final != url:
        _LOGGER.info("Resolved %s -> %s", url, final)
    return final, is_stream
