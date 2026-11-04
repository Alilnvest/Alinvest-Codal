"""جست‌وجوی زنده‌ی اطلاعیه‌ها روی search.codal.ir -- هیچ فهرستِ ثابتی از نماد نگه‌داری نمی‌شود."""

from __future__ import annotations

import os
from typing import Any, Iterator

from . import http
from .endpoints import SEARCH
from .exceptions import CodalError, SymbolNotFoundError
from .models import Letter
from .progress import ProgressCallback, SearchProgress, make_progress
from .utils import is_consolidated_title, normalize_fa, today_jalali, validate_jdate

SEARCH_URL = SEARCH.url
_SEARCH_HEADERS = {"Accept": "application/json, text/plain, */*"}

# TTL کوتاه: اطلاعیه‌های تازه اول اینجا ظاهر می‌شوند؛ کش فقط برای جلوگیری از جست‌وجوی تکراری در یک اجراست.
SEARCH_CACHE_TTL_SECONDS = 2 * 60

# شروعِ پیش‌فرضِ جست‌وجو (تاریخ جلالی). با متغیر محیطی CODAL_DEFAULT_FROM_DATE قابل تغییر است.
DEFAULT_FROM_DATE = "1401/01/01"
DEFAULT_FROM_DATE_ENV = "CODAL_DEFAULT_FROM_DATE"

# برای گرفتنِ یک اطلاعیه با شماره‌ی TracingNo، بازه‌ی گسترده‌تری لازم است (شماره‌های قدیمی).
TRACING_LOOKUP_FROM_DATE = "1390/01/01"

CATEGORY_ANNUAL_FINANCIAL_STATEMENTS = 1
CATEGORY_MONTHLY_ACTIVITY = 3
LETTER_TYPE_INTERIM_FINANCIAL_STATEMENTS = 6
LETTER_TYPE_PORTFOLIO_POSITION = 8
LETTER_TYPE_MONTHLY_ACTIVITY = 58


def _today_jalali() -> str:
    """امروز به تقویم جلالی و به وقتِ تهران؛ دیگر بدونِ jdatetime هم کار می‌کند."""
    return today_jalali()


def default_lookback_from_date() -> str:
    """تاریخِ شروعِ پیش‌فرضِ جست‌وجو (تنها منبعِ حقیقت برای همه‌ی توابع).

    مقدار ثابتِ ``DEFAULT_FROM_DATE`` (۱۴۰۱/۰۱/۰۱) است، مگر اینکه متغیر محیطیِ
    ``CODAL_DEFAULT_FROM_DATE`` تنظیم شده باشد. برای تاریخچه‌ی قدیمی‌تر، from_date را صریح بده.
    """
    raw = os.getenv(DEFAULT_FROM_DATE_ENV) or DEFAULT_FROM_DATE
    return validate_jdate(raw, param_name=DEFAULT_FROM_DATE_ENV)


def _letter_matches(letter: Letter, query: str, *, strict: bool) -> bool:
    """در حالت strict، نماد کوتاه یا نام کامل رسمی باید با query یکی باشد."""
    if not strict:
        return True
    query_norm = normalize_fa(query)
    return normalize_fa(letter.symbol) == query_norm or normalize_fa(letter.company_name) == query_norm


def _check_payload(data: Any) -> dict:
    if not isinstance(data, dict):
        raise CodalError(f"ساختارِ پاسخِ کدال غیرمنتظره بود (نوع: {type(data).__name__}).")
    return data


def _letter_from_raw(raw: dict) -> Letter:
    """ساخت Letter با سازگاری با نصب‌های قدیمی که from_raw ندارند."""
    factory = getattr(Letter, "from_raw", None)
    if callable(factory):
        return factory(raw)
    from .utils import get_ci, to_bool
    def _url(value: Any) -> Any:
        if not value or str(value).startswith(("http://", "https://")):
            return value
        return f"https://www.codal.ir{value}" if str(value).startswith("/") else value

    return Letter(
        tracing_no=get_ci(raw, "TracingNo"),
        symbol=get_ci(raw, "Symbol"),
        company_name=get_ci(raw, "CompanyName"),
        title=get_ci(raw, "Title"),
        letter_code=get_ci(raw, "LetterCode"),
        sent_date_time=get_ci(raw, "SentDateTime") or get_ci(raw, "PublishDateTime"),
        publish_date_time=get_ci(raw, "PublishDateTime"),
        has_html=to_bool(get_ci(raw, "HasHtml")),
        has_excel=to_bool(get_ci(raw, "HasExcel")),
        has_pdf=to_bool(get_ci(raw, "HasPdf")),
        has_xbrl=to_bool(get_ci(raw, "HasXbrl")),
        has_attachment=to_bool(get_ci(raw, "HasAttachment")),
        url=_url(get_ci(raw, "Url")),
        attachment_url=_url(get_ci(raw, "AttachmentUrl")),
        pdf_url=_url(get_ci(raw, "PdfUrl")),
        excel_url=_url(get_ci(raw, "ExcelUrl")),
        raw=raw,
    )


def search_letters(
    symbol: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    category: int = -1,
    letter_type: int = -1,
    audited: bool | None = None,
    max_pages: int | None = None,
    strict_symbol: bool = True,
    title_contains: str | None = None,
    letter_code: str | None = None,
    consolidated: bool | None = None,
    mains: bool = True,
    childs: bool = False,
    consolidatable: bool = True,
    not_consolidatable: bool = True,
    publisher_type: int = 1,
    company_state: int = 0,
    company_type: int = -1,
    length: int = -1,
    limit: int | None = None,
    progress: bool | ProgressCallback | None = True,
    client: Any | None = None,
    use_cache: bool = True,
    raise_if_empty: bool = True,
) -> Iterator[Letter]:
    """جست‌وجوی زنده‌ی اطلاعیه‌های یک نماد؛ صفحه‌به‌صفحه yield می‌کند.

    from_date: پیش‌فرض ``default_lookback_from_date()`` (۱۴۰۱/۰۱/۰۱). to_date: پیش‌فرض امروز.
    raises: ValueError اگر تاریخ‌ها فرمت معتبر نداشته باشند (قبل از هر درخواست).
    """
    if limit is not None and limit < 0:
        raise ValueError("limit باید نامنفی باشد")
    if max_pages is not None and max_pages < 0:
        raise ValueError("max_pages باید نامنفی باشد")
    if limit == 0 or max_pages == 0:
        return
    from_date = default_lookback_from_date() if from_date is None else validate_jdate(from_date, param_name="from_date")
    to_date = _today_jalali() if to_date is None else validate_jdate(to_date, param_name="to_date")

    params: dict[str, Any] = {
        "Symbol": symbol,
        "Category": category,
        "PublisherType": publisher_type,
        "LetterType": letter_type,
        "Length": length,
        "Audited": "true",
        "NotAudited": "true",
        "Mains": str(bool(mains)).lower(),
        "Childs": str(bool(childs)).lower(),
        "Consolidatable": str(bool(consolidatable)).lower(),
        "NotConsolidatable": str(bool(not_consolidatable)).lower(),
        "AuditorRef": -1,
        "CompanyState": company_state,
        "CompanyType": company_type,
        "TracingNo": -1,
        "Publisher": "false",
        "IsNotAudited": "false",
        "FromDate": from_date,
        "ToDate": to_date,
        "PageNumber": 1,
    }
    if audited is True:
        params.update({"Audited": "true", "NotAudited": "false"})
    elif audited is False:
        params.update({"Audited": "false", "NotAudited": "true"})

    http.symbol_pause(symbol)  # فاصله‌ی ۱۵ تا ۲۰ ثانیه‌ای وقتی از نمادی به نمادِ دیگر می‌رویم
    progress_callback = make_progress(progress, label=f"جست‌وجوی {symbol}")
    title_norm = normalize_fa(title_contains) if title_contains else None
    code_norm = normalize_fa(letter_code) if letter_code else None
    page = 1
    fetched_pages = 0
    scanned = 0
    yielded_total = 0
    seen_any_raw = False
    seen_tracing: set[int] = set()

    while True:
        params["PageNumber"] = page
        if client is None:
            resp = http.get(SEARCH_URL, params=params, headers=_SEARCH_HEADERS, cache_ttl=SEARCH_CACHE_TTL_SECONDS,
                            use_cache=use_cache)
        else:
            resp = client.get(SEARCH_URL, params=params, use_cache=use_cache)
        try:
            data = _check_payload(resp.json())
        except ValueError as exc:
            raise CodalError(f"پاسخِ کدال JSON معتبر نبود: {exc}") from exc

        letters_raw = data.get("Letters") or []
        if not letters_raw:
            break
        seen_any_raw = True
        page_count = int(data.get("Page") or 1)

        for raw in letters_raw:
            scanned += 1
            letter = _letter_from_raw(raw)
            if letter.tracing_no is not None:
                tracing = int(letter.tracing_no)
                if tracing in seen_tracing:
                    continue
                seen_tracing.add(tracing)
            if progress_callback:
                progress_callback(
                    SearchProgress(
                        phase="search",
                        page=page,
                        page_count=page_count,
                        scanned=scanned,
                        yielded=yielded_total,
                        current_title=letter.title,
                        current_date=letter.publish_date_time or letter.sent_date_time,
                    )
                )
            if not _letter_matches(letter, symbol, strict=strict_symbol):
                continue
            if title_norm and title_norm not in normalize_fa(letter.title):
                continue
            if code_norm and normalize_fa(letter.letter_code) != code_norm:
                continue
            if consolidated is not None and is_consolidated_title(letter.title) != consolidated:
                continue
            yielded_total += 1
            yield letter
            if limit is not None and yielded_total >= limit:
                if progress_callback:
                    progress_callback(
                        SearchProgress(
                            phase="search", page=page, page_count=page_count, scanned=scanned,
                            yielded=yielded_total, current_title=letter.title,
                            current_date=letter.publish_date_time, done=True,
                        )
                    )
                return

        fetched_pages += 1
        if page >= page_count:
            break
        if max_pages is not None and fetched_pages >= max_pages:
            break
        page += 1

    if progress_callback:
        progress_callback(
            SearchProgress(phase="search", page=page, page_count=max(page, 1), scanned=scanned,
                           yielded=yielded_total, done=True)
        )

    if not seen_any_raw and raise_if_empty:
        raise SymbolNotFoundError(
            f"هیچ اطلاعیه‌ای برایِ {symbol!r} روی کدال پیدا نشد. املایِ نماد رو چک کن.",
            symbol=symbol,
        )


def get_letter(tracing_no: int) -> Letter:
    """اطلاعیه را با شماره‌ی TracingNo می‌گیرد."""
    params = {
        "Symbol": "", "Category": -1, "PublisherType": 1, "LetterType": -1,
        "Length": -1, "Audited": "true", "NotAudited": "true", "Mains": "true",
        "Childs": "false", "Consolidatable": "true", "NotConsolidatable": "true",
        "AuditorRef": -1, "CompanyState": 0, "CompanyType": -1, "TracingNo": tracing_no,
        "Publisher": "false", "IsNotAudited": "false", "FromDate": TRACING_LOOKUP_FROM_DATE,
        "ToDate": _today_jalali(), "PageNumber": 1,
    }
    resp = http.get(SEARCH_URL, params=params, headers=_SEARCH_HEADERS, cache_ttl=SEARCH_CACHE_TTL_SECONDS)
    data = _check_payload(resp.json())
    for raw in data.get("Letters") or []:
        letter = _letter_from_raw(raw)
        if letter.tracing_no is not None and int(letter.tracing_no) == int(tracing_no):
            return letter
    raise SymbolNotFoundError(f"شماره‌ی TracingNo={tracing_no} پیدا نشد.")


def latest_letter(symbol: str, **kwargs: Any) -> Letter:
    """جدیدترین اطلاعیه‌ی منطبق با فیلترها؛ اگر نبود SymbolNotFoundError (نه StopIteration)."""
    kwargs.setdefault("limit", 1)
    found = next(search_letters(symbol, **kwargs), None)
    if found is None:
        raise SymbolNotFoundError(f"هیچ اطلاعیه‌ی منطبقی برای {symbol!r} پیدا نشد.", symbol=symbol)
    return found
