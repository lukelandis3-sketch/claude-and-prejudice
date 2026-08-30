"""Shared HTTP fetching. Stdlib urllib, a real user agent, and a hard timeout."""

import gzip
import urllib.request

USER_AGENT = "thinking-book/0.1 (+https://github.com/lukelandis3-sketch/claude-thinking-book)"
TIMEOUT = 20
MAX_BYTES = 12 * 1024 * 1024


def get(url, accept="*/*", timeout=TIMEOUT):
    """Fetch a URL and return decoded text. Raises on non-http(s) schemes."""
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("only http(s) URLs are supported: %r" % url)

    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": accept, "Accept-Encoding": "gzip"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_BYTES)
        if response.headers.get("Content-Encoding") == "gzip":
            try:
                raw = gzip.decompress(raw)
            except OSError:
                pass
        charset = response.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")
