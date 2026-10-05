"""LanguageTool HTTP client (design: docs/design/M2.md §3.1)."""

import logging
from typing import Any

import httpx

from app.nlp.types import LTMatch

logger = logging.getLogger(__name__)

MAX_REPLACEMENTS = 3


class LanguageToolClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/v2/check"
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def check(self, text: str, language: str = "de-DE") -> list[LTMatch] | None:
        """Matches for `text`, or `None` when the server is unreachable or misbehaves."""
        try:
            response = self._client.post(self._url, data={"text": text, "language": language})
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
            return [_parse_match(m) for m in payload.get("matches", [])]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning("LanguageTool unavailable: %s", exc)
            return None


def _parse_match(raw: dict[str, Any]) -> LTMatch:
    rule = raw.get("rule") or {}
    category = rule.get("category") or {}
    return LTMatch(
        offset=int(raw["offset"]),
        length=int(raw["length"]),
        rule_id=str(rule.get("id", "")),
        category=str(category.get("id") or category.get("name") or ""),
        message=str(raw.get("message", "")),
        replacements=[
            str(r["value"])
            for r in (raw.get("replacements") or [])[:MAX_REPLACEMENTS]
            if "value" in r
        ],
    )
