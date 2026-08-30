"""Shared HTTP fetching. Stdlib urllib, a real user agent, and a hard timeout."""

import gzip
import io
import urllib.error
import urllib.request

USER_AGENT = "thinking-book/0.1 (+https://github.com/lukelandis3-sketch/claude-thinking-book)"
TIMEOUT = 20
MAX_BYTES = 12 * 1024 * 1024


class FetchError(OSError):
    pass


def _bounded_gunzip(raw, limit=MAX_BYTES):
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
            data = compressed.read(limit + 1)
    except OSError as exc:
        raise FetchError("the server returned invalid gzip data") from exc
    if len(data) > limit:
        raise FetchError("decompressed response exceeds the %d MB safety limit" % (limit // 1024 // 1024))
    return data


def get(url, accept="*/*", timeout=TIMEOUT):
    """Fetch a URL and return decoded text. Raises on non-http(s) schemes."""
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("only http(s) URLs are supported: %r" % url)

    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": accept, "Accept-Encoding": "gzip"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise FetchError(
                    "response from %s exceeds the %d MB safety limit"
                    % (url, MAX_BYTES // 1024 // 1024)
                )
            encodings = (response.headers.get("Content-Encoding") or "").casefold()
            if "gzip" in (value.strip() for value in encodings.split(",")):
                raw = _bounded_gunzip(raw)
            charset = response.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        raise FetchError("HTTP %s while fetching %s" % (exc.code, url)) from exc
    except urllib.error.URLError as exc:
        raise FetchError("could not fetch %s: %s" % (url, exc.reason)) from exc
    return raw.decode(charset, errors="replace")
