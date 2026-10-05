"""Fetching and extracting article text (design: docs/design/M3.md §1.4)."""

import ipaddress
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import trafilatura

MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_S = 10.0
MIN_CHARS = 200
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)

GERMAN_STOPWORDS = frozenset(
    """der die das den dem des ein eine einen einem einer eines und oder aber nicht kein keine
    ist sind war waren wird werden wurde wurden hat haben hatte hatten sein seine ihr ihre ich du er
    sie es wir mit von zu zum zur auf in im an am aus bei nach für über um als wie auch noch nur
    schon dass wenn dann so sich mich dich uns euch ihnen ihm ihn man mehr sehr diese dieser dieses
    können kann muss soll will""".split()
)
GERMAN_MIN_SHARE = 0.10


class ExtractionFailed(Exception):
    """The article could not be fetched or yielded no text; `reason` is shown to the learner."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ExtractedText:
    title: str
    text: str
    url: str


def german_share(text: str) -> float:
    """Share of German stopwords among the words of `text`."""
    words = re.findall(r"[^\W\d_]+", text.casefold())
    if not words:
        return 0.0
    return sum(w in GERMAN_STOPWORDS for w in words) / len(words)


def source_language(text: str) -> str:
    """`"de"` when the text looks German, else `"other"` (treated as a translation source)."""
    return "de" if german_share(text) >= GERMAN_MIN_SHARE else "other"


def _resolve(host: str) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def check_public_host(host: str, resolve: Callable[[str], list[str]] = _resolve) -> None:
    """Refuse hosts that resolve to loopback, private, link-local or otherwise non-public
    addresses, so a pasted link can't make the server probe its own network (SSRF)."""
    try:
        addresses = resolve(host)
    except (OSError, UnicodeError):
        raise ExtractionFailed("The link's host could not be resolved") from None
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if not ip.is_global:
            raise ExtractionFailed("Links to local or private network addresses are not allowed")


def fetch_article(url: str, transport: httpx.BaseTransport | None = None) -> ExtractedText:
    """Fetch and extract an article. `transport` is for tests; without it every request,
    redirects included, is checked with `check_public_host`."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ExtractionFailed("The link must be an http(s) URL")

    def guard(request: httpx.Request) -> None:
        if request.url.scheme not in ("http", "https"):
            raise ExtractionFailed("The link must be an http(s) URL")
        check_public_host(request.url.host)

    hooks = {"request": [guard]} if transport is None else {}
    try:
        with httpx.Client(
            event_hooks=hooks,
            timeout=TIMEOUT_S,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "de,en;q=0.5"},
            follow_redirects=True,
            transport=transport,
        ) as client:
            with client.stream("GET", url) as response:
                if response.status_code >= 400:
                    raise ExtractionFailed(
                        f"The page answered with HTTP {response.status_code} "
                        "(it may be behind a paywall or block automated access)"
                    )
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise ExtractionFailed("The page is larger than 2 MB")
    except httpx.TimeoutException:
        raise ExtractionFailed("The page took too long to answer") from None
    except httpx.HTTPError as exc:
        raise ExtractionFailed(f"The page could not be fetched ({type(exc).__name__})") from None

    document = trafilatura.bare_extraction(
        bytes(body),
        url=url,
        with_metadata=True,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
    )
    text = (getattr(document, "text", None) or "").strip()
    if len(text) < MIN_CHARS:
        raise ExtractionFailed(
            "No article text could be extracted (the page may be paywalled or empty): "
            "paste the text instead"
        )
    title = (getattr(document, "title", None) or "").strip() or parsed.netloc
    return ExtractedText(title=title, text=text, url=url)
