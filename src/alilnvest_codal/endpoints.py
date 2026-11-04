"""Codal endpoint definitions kept in one place for testing and overrides."""
from __future__ import annotations

from dataclasses import dataclass

from .config import DEFAULT_USER_AGENT


@dataclass(frozen=True, slots=True)
class CodalEndpoint:
    """An endpoint that can be replaced by callers for tests or mirrors."""

    url: str
    method: str = "GET"

    def fetch(self) -> str:
        """Return the endpoint URL; transport is deliberately client-owned."""
        return self.url


SEARCH = CodalEndpoint("https://search.codal.ir/api/search/v2/q")
REPORT = CodalEndpoint("https://www.codal.ir")

BROWSER_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
    "Referer": "https://www.codal.ir/",
}
