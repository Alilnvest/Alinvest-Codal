"""Small stateful facade compatible with the useful part of کتابخانه‌های مشابه's API.

Unlike کتابخانه‌های مشابه's static issuer catalogue, this facade validates issuers through
Codal's live search endpoint, so newly listed symbols are not rejected locally.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .reports import fund_portfolio as _fund_portfolio
from .reports import income_statement as _income_statement
from .reports import balance_sheet as _balance_sheet
from .search import search_letters
from .utils import validate_jdate


@dataclass(slots=True)
class Codal:
    """Reusable query object for applications migrating from ``کتابخانه‌های مشابه.Codal``."""

    issuer: str
    from_jdate: str
    to_jdate: str | None = None

    def __post_init__(self) -> None:
        self.from_jdate = validate_jdate(self.from_jdate, param_name="from_jdate")
        if self.to_jdate is not None:
            self.to_jdate = validate_jdate(self.to_jdate, param_name="to_jdate")

    def letter(self, **kwargs: Any) -> list[Any]:
        """Return all matching letters using live search and pagination."""
        options = dict(kwargs)
        options.setdefault("from_date", self.from_jdate)
        if self.to_jdate is not None:
            options.setdefault("to_date", self.to_jdate)
        return list(search_letters(self.issuer, **options))

    def income_statement(self, **kwargs: Any) -> list[Any]:
        """Fetch income statements for the current issuer and date range."""
        options = dict(kwargs)
        options.setdefault("from_date", self.from_jdate)
        if self.to_jdate is not None:
            options.setdefault("to_date", self.to_jdate)
        return _income_statement(self.issuer, **options)

    def balance_sheet(self, **kwargs: Any) -> list[Any]:
        """Fetch balance sheets for the current issuer and date range."""
        options = dict(kwargs)
        options.setdefault("from_date", self.from_jdate)
        if self.to_jdate is not None:
            options.setdefault("to_date", self.to_jdate)
        return _balance_sheet(self.issuer, **options)

    def fund_monthly_portfolio(self, **kwargs: Any) -> list[Any]:
        """Fetch fund portfolio reports using the existing Excel fallback."""
        options = dict(kwargs)
        options.setdefault("from_date", self.from_jdate)
        if self.to_jdate is not None:
            options.setdefault("to_date", self.to_jdate)
        return _fund_portfolio(self.issuer, **options)

    def set_issuer(self, issuer: str) -> "Codal":
        """Change the symbol without rebuilding the date query."""
        if not issuer or not issuer.strip():
            raise ValueError("issuer must not be empty")
        self.issuer = issuer.strip()
        return self


__all__ = ["Codal"]
