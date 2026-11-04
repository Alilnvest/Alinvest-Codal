"""استخراجِ متغیرِ جاوااسکریپتِ `datasource` از صفحه‌ی گزارشِ کدال.

با json.JSONDecoder.raw_decode دقیقاً مرزِ آبجکتِ JSON پیدا می‌شود، حتی اگر متنِ گزارش
یک ';' داشته باشد (رجکسِ حریصانه این را خراب می‌کرد).

اگر `store` داده شود، پاسخ‌ها از آنجا خوانده و در آنجا ذخیره می‌شوند؛ یعنی اجرای دوباره
فقط اطلاعیه‌های جدید را دانلود می‌کند (نگاه کن به store.py).
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from . import http
from .exceptions import CodalNotFoundError, DataSourceError
from .models import BASE_URL, DataSourceResult, Letter

_DATASOURCE_MARKERS = ("var datasource = ", "var datasource=")
# هر فاصله‌ای بین var، نام و '=' (کدال گاهی تب یا دو فاصله می‌گذارد).
_DATASOURCE_RE = re.compile(r"\bvar\s+datasource\s*=\s*")


def extract_datasource(html: str) -> Any | None:
    """آبجکتِ datasource را برمی‌گرداند؛ اگر متغیر یا JSONِ معتبری نباشد None."""
    if not html:
        return None
    for match in _DATASOURCE_RE.finditer(html):
        try:
            obj, _end = json.JSONDecoder().raw_decode(html, match.end())
        except json.JSONDecodeError:
            continue
        return obj if isinstance(obj, dict) else None
    return None


def _no_datasource(letter: Letter, url: str | None) -> DataSourceError:
    return DataSourceError(
        f"متغیرِ 'datasource' توی صفحه‌ی گزارش پیدا نشد (TracingNo={letter.tracing_no}). "
        "شاید این اطلاعیه صورتِ مالیِ ساختاریافته نداره یا لینک منقضی شده.",
        tracing_no=letter.tracing_no,
        url=url,
    )


def report_request(letter: Letter, sheet_id: str | None = None) -> tuple[str, dict[str, list[str]]]:
    """آدرس و پارامترهای صفحه‌ی گزارشِ یک شیت.

    هر شکلی از کلیدِ SheetId که در خودِ لینکِ کدال باشد (sheetId، SHEETID، ...) اول حذف می‌شود؛
    قبلاً اگر لینک «sheetId» کوچک داشت، دو کلید با هم فرستاده می‌شد و کدال یکی را برمی‌داشت.
    """
    if not letter.url:
        raise DataSourceError(
            f"اطلاعیه‌ی TracingNo={letter.tracing_no} لینکِ صفحه‌ی گزارش ندارد.",
            tracing_no=letter.tracing_no,
        )
    absolute = urljoin(BASE_URL + "/", letter.url)
    urlp = urlparse(absolute)
    query = parse_qs(urlp.query)
    if sheet_id is not None:
        for key in [k for k in query if k.lower() == "sheetid"]:
            del query[key]
        query["SheetId"] = [str(sheet_id)]
    return urljoin(BASE_URL + "/", urlp.path), query


def _get_report_response(letter: Letter, sheet_id: str | None, *, client: Any | None, use_cache: bool) -> Any:
    url, query = report_request(letter, sheet_id)
    try:
        if client is None:
            return http.get(url, params=query, timeout=30, use_cache=use_cache)
        return client.get(url, params=query, use_cache=use_cache)
    except CodalNotFoundError as exc:
        # ۴۰۴ یعنی «این اطلاعیه/صفحه نیست»، نه خطای شبکه؛ حلقه‌های fetch فقط DataSourceError/NetworkError
        # را رد می‌کنند، پس بدونِ این تبدیل یک اطلاعیه‌ی ۴۰۴ کلِ فراخوانی را می‌انداخت. در store هم ثبت
        # نمی‌شود چون ممکن است موقتی باشد.
        raise _no_datasource(letter, url) from exc


def fetch_report_html(
    letter: Letter,
    *,
    sheet_id: str | None = None,
    client: Any | None = None,
    use_cache: bool = True,
) -> tuple[str, str]:
    """HTMLِ خامِ صفحه‌ی گزارشِ یک شیت و آدرسِ نهایی‌اش: (html, url).

    برای شیت‌هایی که datasource ندارند (نظر حسابرس، اعضای هیئت مدیره، شرکت‌های فرعی) لازم است.
    با کشِ http همان پاسخی برمی‌گردد که get_datasource گرفته بود؛ درخواستِ دوم فرستاده نمی‌شود.
    """
    resp = _get_report_response(letter, sheet_id, client=client, use_cache=use_cache)
    return resp.text, str(getattr(resp, "url", "") or letter.url)


def get_datasource(
    letter: Letter,
    *,
    sheet_id: str | None = None,
    client: Any | None = None,
    use_cache: bool = True,
    store: Any | None = None,
) -> dict:
    """دیتاسورسِ توکارِ صفحه‌ی گزارش را می‌گیرد.

    sheet_id: "0" = ترازنامه، "1" = سودوزیان (طبق کتابخانه‌های مشابه)؛ None یعنی شیتِ پیش‌فرضِ لینک.
    store: اختیاری؛ یک SQLiteStore. اگر پاسخ آنجا باشد شبکه لازم نیست، و اطلاعیه‌های بدون
    datasource هم به‌یادماندنی‌اند تا هر بار دوباره درخواست نشوند.
    """
    if store is not None:
        hit = store.get_datasource(letter.tracing_no, sheet_id)
        if hit is not None:
            if hit.status == "absent":
                raise _no_datasource(letter, letter.url)
            if not isinstance(hit.payload, dict):
                raise _no_datasource(letter, letter.url)
            return hit.payload

    resp = _get_report_response(letter, sheet_id, client=client, use_cache=use_cache)
    data = extract_datasource(resp.text)
    if data is None:
        if store is not None:
            store.put_absent(letter.tracing_no, sheet_id, symbol=letter.symbol)
        raise _no_datasource(letter, str(resp.url))
    if not isinstance(data, dict):
        raise _no_datasource(letter, str(resp.url))
    if store is not None:
        store.put_datasource(letter.tracing_no, sheet_id, data, symbol=letter.symbol)
    return data


def get_datasource_result(
    letter: Letter,
    *,
    sheet_id: str | None = None,
    client: Any | None = None,
    use_cache: bool = True,
    store: Any | None = None,
) -> DataSourceResult:
    """نسخه‌ی non-raising برای batchها؛ خطا را کنار همان نامه نگه می‌دارد."""
    try:
        data = get_datasource(
            letter, sheet_id=sheet_id, client=client,
            use_cache=use_cache, store=store,
        )
        return DataSourceResult(status="success", letter=letter, data=data, sheet_id=sheet_id)
    except Exception as exc:  # noqa: BLE001 - قرارداد این تابع برگرداندن خطاست
        return DataSourceResult(
            status="error", letter=letter, error=f"{type(exc).__name__}: {exc}", sheet_id=sheet_id,
        )
