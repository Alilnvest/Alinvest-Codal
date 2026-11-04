"""Operational diagnostics for long-running Codal bots."""
from __future__ import annotations

import time
from typing import Any

from . import http
from .search import SEARCH_URL


def health_check(*, timeout: float = 10.0) -> dict[str, Any]:
    """Check Codal reachability and HTTP status without raising to the caller."""
    started = time.perf_counter()
    base: dict[str, Any] = {"service": "search.codal.ir"}
    try:
        response = http.get(SEARCH_URL, params={"Symbol": "", "Category": -1, "LetterType": -1, "PageNumber": 1},
                            timeout=timeout, use_cache=False)
        status_code = int(getattr(response, "status_code", 0) or 0)
        base.update({"ok": 200 <= status_code < 400, "status_code": status_code,
                     "elapsed_seconds": round(time.perf_counter() - started, 3)})
        if not base["ok"]:
            base.update({"error_type": "HTTPStatusError", "error": f"HTTP {status_code}"})
        return base
    except Exception as exc:
        base.update({"ok": False, "elapsed_seconds": round(time.perf_counter() - started, 3),
                     "error_type": type(exc).__name__, "error": str(exc)})
        return base


def explain(result: dict[str, Any]) -> str:
    if result.get("ok"):
        return "کدال در دسترس است."
    text = f"{result.get('error_type', '')} {result.get('error', '')}".lower()
    if "reset" in text or "aborted" in text or "remote end closed" in text:
        return "اتصال از سمت سرور بسته یا ریست شد. این معمولاً نشانه‌ی بلاکِ موقتِ سرور بعد از درخواست‌های زیاد است: چند ساعت صبر کنید؛ بعد از آن از --store استفاده کنید تا داده‌ها دوباره درخواست نشوند."
    if "timeout" in text or "timed out" in text:
        return "پاسخ سرور دیر رسید. اتصال اینترنت را بررسی کنید یا بعداً دوباره امتحان کنید."
    if any(word in text for word in ("name or service", "nodename", "failed to resolve", "getaddrinfo")):
        return "نام سرور پیدا نشد (DNS). اتصال اینترنت یا تنظیمات DNS/پروکسی دستگاه را بررسی کنید."
    if "ratelimiterror" in text or "429" in text or "too many" in text:
        return "کدال تعدادِ درخواست‌ها را محدود کرده (HTTP 429). چند دقیقه صبر کنید و دوباره اجرا کنید؛ اجراهای پشت‌سرهم را کم کنید و از --store استفاده کنید تا داده‌ها دوباره درخواست نشوند."
    if any(code in text for code in ("500", "502", "503", "504")):
        return "سرور کدال خطای موقت داده است. بعداً دوباره امتحان کنید."
    return "علت مشخص نشد؛ پیام خطای بالا را بررسی کنید."


__all__ = ["health_check", "explain"]
