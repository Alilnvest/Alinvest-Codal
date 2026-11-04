"""Stable 1.0 public API.

The legacy report engine remains underneath so existing advanced features keep
working while callers receive keyword-only, injectable entry points.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .config import CodalConfig
from .http import CodalHTTPClient
from .reports import balance_sheet as _balance_sheet
from .reports import income_statement as _income_statement


def income_statement(
    symbol: str,
    *,
    from_date: str | None = None,
    max_reports: int | None = None,
    use_cache: bool = True,
    client: CodalHTTPClient | None = None,
    **kwargs: Any,
) -> list[Any]:
    """Return income statements, preserving the mature Codal parser behavior."""
    options = dict(kwargs)
    if from_date is not None:
        options["from_date"] = from_date
    if max_reports is not None:
        options["max_reports"] = max_reports
    options["use_cache"] = use_cache
    options["client"] = client
    options.setdefault("max_concurrency", 5)
    return _income_statement(symbol, **options)


def balance_sheet(
    symbol: str,
    *,
    from_date: str | None = None,
    max_reports: int | None = None,
    use_cache: bool = True,
    client: CodalHTTPClient | None = None,
    **kwargs: Any,
) -> list[Any]:
    """Return balance sheets with the same compatibility guarantees as 0.9."""
    options = dict(kwargs)
    if from_date is not None:
        options["from_date"] = from_date
    if max_reports is not None:
        options["max_reports"] = max_reports
    options["use_cache"] = use_cache
    options["client"] = client
    options.setdefault("max_concurrency", 5)
    return _balance_sheet(symbol, **options)


async def async_income_statement(symbol: str, **kwargs: Any) -> list[Any]:
    """Async facade for applications that need a non-blocking call site."""
    return await asyncio.to_thread(income_statement, symbol, **kwargs)


async def async_balance_sheet(symbol: str, **kwargs: Any) -> list[Any]:
    """Async facade for applications that need a non-blocking call site."""
    return await asyncio.to_thread(balance_sheet, symbol, **kwargs)


__all__ = ["income_statement", "balance_sheet", "async_income_statement", "async_balance_sheet", "CodalConfig"]
