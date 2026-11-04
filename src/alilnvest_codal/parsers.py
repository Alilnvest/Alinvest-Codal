"""Pure parsing helpers (این ماژول هیچ درخواستی نمی‌زند).

نسخه‌ی قبلی توابعِ تکراری و `html_tables` داشت. `html_tables` با pandas جدید روی رشته‌ی HTML
خطای FileNotFoundError می‌داد و هیچ‌جا استفاده نمی‌شد، پس حذف شد. منطقِ اصلی در datasource است.
"""
from __future__ import annotations

from typing import Any

from .datasource import extract_datasource as _find_datasource
from .exceptions import CodalParsingError

# نام قدیمی؛ برای سازگاری نگه داشته شده و زیرکلاسِ CodalError است.
ParsingError = CodalParsingError


def extract_datasource(html: str) -> dict[str, Any]:
    """آبجکتِ JSONِ `var datasource = ...` را برمی‌گرداند؛ اگر نبود یا dict نبود CodalParsingError می‌دهد."""
    value = _find_datasource(html)
    if value is None:
        raise CodalParsingError("Codal datasource was not found or is not valid JSON")
    if not isinstance(value, dict):
        raise CodalParsingError("Codal datasource is not a JSON object")
    return value
