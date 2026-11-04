"""لایه‌یِ بالا -- ترکیبِ search + datasource + frames، با فیلترِ عنوان برایِ جلوگیری از درخواستِ بی‌خود."""

from __future__ import annotations

import logging
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from .datasource import fetch_report_html, get_datasource
from .sheets import auditor_text_from_html
from .excel import fetch_attachment_tables
from .exceptions import CodalError, DataSourceError, ExcelParseError, NetworkError, RateLimitError
from .frames import cells_to_dataframe, cells_to_grid
from .models import FinancialReport, PortfolioReport, FinancialSnapshot
from .progress import ProgressCallback, SearchProgress, make_progress
from .analytics import clean_table, compare_reports
from .search import (
    CATEGORY_ANNUAL_FINANCIAL_STATEMENTS,
    CATEGORY_MONTHLY_ACTIVITY,
    LETTER_TYPE_INTERIM_FINANCIAL_STATEMENTS,
    LETTER_TYPE_MONTHLY_ACTIVITY,
    LETTER_TYPE_PORTFOLIO_POSITION,
    search_letters,
)
from .utils import extract_period_end, get_ci, is_consolidated_title, normalize_alias, normalize_fa

logger = logging.getLogger("alilnvest_codal.reports")

SHEET_ID_BALANCE_SHEET_STANDALONE = "0"  # تأییدشده: title_Fa="صورت وضعیت مالی", aliasName="BalanceSheet"
SHEET_ID_BALANCE_SHEET_CONSOLIDATED = "14"  # تأییدشده با diagnose_cash_flow_sheets.py رویِ فملی: title_Fa="صورت وضعیت مالی تلفیقی", aliasName="ConsolidatedBalanceSheet"
SHEET_ID_INCOME_STATEMENT_STANDALONE = "1"  # تأییدشده: title_Fa="صورت سود و زیان", aliasName="IncomeStatement"
SHEET_ID_INCOME_STATEMENT_CONSOLIDATED = "13"  # تأییدشده با diagnose_cash_flow_sheets.py رویِ فملی: title_Fa="صورت سود و زیان تلفیقی", aliasName="ConsolidatedIncomeStatement"
SHEET_ID_CASH_FLOW_STATEMENT_STANDALONE = "9"  # تأییدشده: title_Fa="صورت جریان های نقدی", aliasName="CashFlow"
SHEET_ID_CASH_FLOW_STATEMENT_CONSOLIDATED = "15"  # تأییدشده: title_Fa="صورت جریان های نقدی تلفیقی", aliasName="ConsolidatedCashFlow"
# حقوق مالکانه و سود جامع: نسخه‌ی غیرتلفیقی (شرکت اصلی) و تلفیقی (گروه) شناسه‌ی جدا دارند.
# * غیرتلفیقی: 1060 (ChangesInPropertyRights) و 1058 (ComprehensiveIncomeStatement)؛ تأییدشده روی
#   اطلاعیه‌ی غیرتلفیقی فملی (TracingNo=1581814).
# * تلفیقی: 1099 (ConsolidatedChangesInPropertyRights) و 1097 (ConsolidatedComprehensiveIncomeStatement)؛
#   تأییدشده با منوی شیت‌های خودِ کدال روی فولاد (TracingNo=1574326، سالانه‌ی ۱۴۰۴) و وبملت
#   (TracingNo=1278431، میان‌دوره‌ی ۱۴۰۳/۰۶/۳۱).
# تا نسخه‌ی 1.4.6 برای اطلاعیه‌ی تلفیقی هم 1060/1058 گرفته می‌شد؛ یعنی ارقامِ *شرکت اصلی* با برچسبِ
# «تلفیقی» برمی‌گشت و با ترازنامه‌ی تلفیقی مقایسه می‌شد (فیکسچرِ equity_1404_standalone_raw.json
# همین را نشان می‌دهد: عنوان تلفیقی، aliasName = ChangesInPropertyRights).
SHEET_ID_EQUITY_CHANGES_STATEMENT_STANDALONE = "1060"  # aliasName="ChangesInPropertyRights"
SHEET_ID_EQUITY_CHANGES_STATEMENT_CONSOLIDATED = "1099"  # aliasName="ConsolidatedChangesInPropertyRights"
SHEET_ID_COMPREHENSIVE_INCOME_STATEMENT_STANDALONE = "1058"  # aliasName="ComprehensiveIncomeStatement"
SHEET_ID_COMPREHENSIVE_INCOME_STATEMENT_CONSOLIDATED = "1097"  # aliasName="ConsolidatedComprehensiveIncomeStatement"

# نام‌های قدیمی برای سازگاری با کدهای قبلی نگه داشته شده‌اند (مقدارِ غیرتلفیقی، مثل قبل).
SHEET_ID_EQUITY_CHANGES_STATEMENT = SHEET_ID_EQUITY_CHANGES_STATEMENT_STANDALONE
SHEET_ID_COMPREHENSIVE_INCOME_STATEMENT = SHEET_ID_COMPREHENSIVE_INCOME_STATEMENT_STANDALONE

# دو تا مجموعه‌یِ کمکی -- تشخیصِ اینکه یه sheet_idِ resolve‌شده «خودِ صورتِ
# سود و زیان»ه (چه غیرتلفیقی چه تلفیقی)، برایِ رد نشدنِ اشتباهیِ محتوایِ
# واقعی‌شون تویِ گاردِ ضدِّ fallback (نگاه کن به fetch_financial_reports).
_INCOME_STATEMENT_SHEET_IDS = frozenset(
    {SHEET_ID_INCOME_STATEMENT_STANDALONE, SHEET_ID_INCOME_STATEMENT_CONSOLIDATED}
)


def _standalone_or_consolidated(standalone_id: str, consolidated_id: str) -> Callable[[str], str]:
    """سازنده‌یِ selectorِ عمومی -- برایِ اینکه سودوزیان/ترازنامه/جریانِ نقد کدِ تکراری نداشته باشن."""
    def _pick(title: str) -> str:
        return consolidated_id if is_consolidated_title(title) else standalone_id
    return _pick


_balance_sheet_sheet_id = _standalone_or_consolidated(
    SHEET_ID_BALANCE_SHEET_STANDALONE, SHEET_ID_BALANCE_SHEET_CONSOLIDATED
)
_income_statement_sheet_id = _standalone_or_consolidated(
    SHEET_ID_INCOME_STATEMENT_STANDALONE, SHEET_ID_INCOME_STATEMENT_CONSOLIDATED
)
_cash_flow_sheet_id = _standalone_or_consolidated(
    SHEET_ID_CASH_FLOW_STATEMENT_STANDALONE, SHEET_ID_CASH_FLOW_STATEMENT_CONSOLIDATED
)
_equity_changes_sheet_id = _standalone_or_consolidated(
    SHEET_ID_EQUITY_CHANGES_STATEMENT_STANDALONE,
    SHEET_ID_EQUITY_CHANGES_STATEMENT_CONSOLIDATED,
)
_comprehensive_income_sheet_id = _standalone_or_consolidated(
    SHEET_ID_COMPREHENSIVE_INCOME_STATEMENT_STANDALONE,
    SHEET_ID_COMPREHENSIVE_INCOME_STATEMENT_CONSOLIDATED,
)


# ⚠️ نتیجه‌گیریِ قطعی (نه فقط حدس) از دو دورِ تشخیصِ زنده رویِ فملی با
# diagnose_sheets.py: این چهار sheet_id توی قالبِ گزارشِ مالی
# ("FinancialStatement-Consolidated-Listed-Product-V9") اصلاً وجود ندارن --
# رویِ گزارشِ فصلیِ حسابرسی‌نشده بی‌سروصدا فال‌بک به صورتِ سود و زیان کردن،
# رویِ گزارشِ سالانه‌یِ حسابرسی‌شده صریح DataSourceError دادن (یعنی هیچ
# sheetی با این کد نیست). این یعنی مشکل صرفاً «عددِ اشتباه» نیست -- این
# داده‌ها (لیستِ زیرمجموعه‌ها، اعضایِ هیئتِ‌مدیره) اصلاً بخشِ sheetهایِ
# JSON datasourceِ این نوع اطلاعیه نیستن. تأییدشده: این‌ها فقط تویِ ضمیمه‌ی
# **اکسل** خودِ همون letter هستن (با اسمِ دقیقِ «شرکت های فرعی»/«شرکت های
# وابسته»/«اعضای هیئت مدیره») -- از ۰.۸.۹، subsidiary_companies/
# affiliated_companies/board_members از fetch_excel_sheet استفاده می‌کنن،
# نه از این sheet_idها. این سه ثابت فقط برایِ سازگاریِ تاریخی/مرجع نگه
# داشته شدن؛ SHEET_ID_AUDITOR_OPINION هنوز واقعاً استفاده می‌شه.
SHEET_ID_AUDITOR_OPINION = "19"
# سقفِ پیش‌فرضِ تعدادِ ضمیمه‌یِ اکسلی که fetch_excel_sheet برایِ پیدا کردنِ یه شیت
# دانلود می‌کنه (هر کدوم چندثانیه). ۸ اطلاعیه‌یِ اخیر (~۲ سال) برایِ ساختارِ
# مالکیت/هیئت‌مدیره کافیه.
DEFAULT_MAX_ATTACHMENT_SCAN = 8

SHEET_ID_SUBSIDIARY_COMPANIES = "51"  # ⚠️ دیگه استفاده نمی‌شه -- نگاه کن به fetch_excel_sheet
SHEET_ID_AFFILIATED_COMPANIES = "52"  # ⚠️ دیگه استفاده نمی‌شه -- نگاه کن به fetch_excel_sheet
SHEET_ID_BOARD_MEMBERS = "30"  # ⚠️ دیگه استفاده نمی‌شه -- نگاه کن به fetch_excel_sheet

# محتوایِ «امضاء»یِ صورتِ سود و زیان -- برایِ تشخیصِ اینکه یه sheet_idِ
# نامعتبر/اشتباه بی‌سروصدا به‌جایِ خطا، محتوایِ صورتِ سود و زیانِ پیش‌فرض
# برگردونده (دقیقاً چیزی که تستِ زنده رویِ فملی برایِ sheet_id=51/52/30/19
# نشون داد -- هر چهارتا، بدونِ خطا، عیناً همون سلول‌هایِ sheet_id=1 رو
# برگردوندن). normalize_fa چون کدال گاهی «ي» عربی و گاهی «ی» فارسی می‌ذاره.
_INCOME_STATEMENT_SIGNATURE = normalize_fa("درآمدهای عملیاتی")


def _looks_like_income_statement_fallback(text_or_cells: str) -> bool:
    return _INCOME_STATEMENT_SIGNATURE in normalize_fa(text_or_cells)


# aliasName هر sheet، همان برچسبی که خودِ کدال برمی‌گرداند (با diagnose_*.py تأیید شده).
# وقتی sheet_id نامعتبر است، کدال بی‌سروصدا صورتِ سود و زیان (sheet 1) را برمی‌گرداند.
# این جدول کمک می‌کند بفهمیم «این پاسخ خودِ همان صورتِ خواسته‌شده است یا نه» از روی هویتِ خودِ صفحه.
_SHEET_ALIAS_BY_ID: dict[str, str] = {
    "0": "BalanceSheet",
    "14": "ConsolidatedBalanceSheet",
    "1": "IncomeStatement",
    "13": "ConsolidatedIncomeStatement",
    "9": "CashFlow",
    "15": "ConsolidatedCashFlow",
    "1060": "ChangesInPropertyRights",
    "1099": "ConsolidatedChangesInPropertyRights",
    "1058": "ComprehensiveIncomeStatement",
    "1097": "ConsolidatedComprehensiveIncomeStatement",
}
_KNOWN_STATEMENT_ALIASES = frozenset(_SHEET_ALIAS_BY_ID.values())
_KNOWN_STATEMENT_ALIASES_NORM = {normalize_alias(a): a for a in _KNOWN_STATEMENT_ALIASES}


def _page_aliases(ds: Any) -> set[str]:
    """aliasNameِ همه‌ی sheetهای یک datasource (خالی اگر کدال نداده باشد)."""
    aliases: set[str] = set()
    for sheet in get_ci(ds, "sheets") or []:
        alias = get_ci(sheet, "aliasName") if isinstance(sheet, dict) else None
        if alias:
            aliases.add(str(alias))
    return aliases


def _fallback_reason(ds: Any, sheet_id: str, cells: list[dict]) -> str | None:
    """اگر پاسخ fallbackِ بی‌سروصدا است، دلیلِ متنی؛ وگرنه None.

    اولویت با هویتِ خودِ صفحه (aliasName) است. فقط وقتی aliasName نداریم یا ناشناخته است،
    به جستجوی عبارتِ «درآمدهای عملیاتی» در متن برمی‌گردیم. این جستجو قبلاً یادداشت‌های
    صورتِ جریانِ نقد را هم می‌دید و یک صفحه‌ی درست را رد می‌کرد.
    """
    expected = _SHEET_ALIAS_BY_ID.get(str(sheet_id))
    aliases = _page_aliases(ds)
    if expected is not None and aliases:
        normalized = {normalize_alias(a) for a in aliases}
        if normalize_alias(expected) in normalized:
            return None
        other = sorted(_KNOWN_STATEMENT_ALIASES_NORM[n] for n in normalized if n in _KNOWN_STATEMENT_ALIASES_NORM)
        if other:
            return f"کدال به‌جای {expected} صورتِ {', '.join(other)} را برگرداند"
    blob = " ".join(str(get_ci(c, "value")) for c in cells if get_ci(c, "value") is not None)
    if blob and _looks_like_income_statement_fallback(blob):
        return "متنِ صفحه شبیهِ صورتِ سود و زیان است (aliasName در پاسخ نبود)"
    return None

# پرست‌ستِ همه‌ی صورت‌هایِ اصلیِ row_code -- برایِ fetch_company_snapshot(sheet_ids="full").
# هر سه‌تایِ سودوزیان/ترازنامه/جریانِ نقد selectorن (نه رشته‌ی ثابت) -- خودکار
# بینِ تلفیقی/غیرتلفیقی بر اساسِ عنوانِ letter انتخاب می‌کنن.
FULL_STATEMENT_SHEET_IDS: dict[str, str | Callable[[str], str]] = {
    "balance_sheet": _balance_sheet_sheet_id,
    "income_statement": _income_statement_sheet_id,
    "comprehensive_income_statement": _comprehensive_income_sheet_id,
    "cash_flow_statement": _cash_flow_sheet_id,
    "equity_changes_statement": _equity_changes_sheet_id,
}

# کلیدواژه‌هایِ عنوان -- همون الگویِ اثبات‌شده‌ای که تویِ منابع پژوهشی قبلی هم بود.
# قبلِ گرفتنِ datasource چک می‌شن تا برایِ اطلاعیه‌هایی که اصلاً جدول ندارن
# (آگهیِ دعوت، گزارشِ فعالیتِ ماهانه، شفاف‌سازی...) درخواستِ اضافه نزنیم.
_FINANCIAL_TITLE_MARKERS = ("صورت‌های مالی", "اطلاعات و صورت‌های مالی")
_PORTFOLIO_TITLE_MARKERS = ("صورت وضعیت پورتفوی",)
# مقایسه بعد از normalize_fa: «صورتهای مالی»، «صورت های مالی» و «صورت‌هاي مالي» هم شناخته می‌شوند.
# قبلاً فقط شکلِ دقیقِ با نیم‌فاصله پذیرفته می‌شد و اطلاعیه‌ای با املای دیگر بی‌صدا کنار می‌رفت.
_FINANCIAL_TITLE_MARKERS_NORM = tuple(normalize_fa(m) for m in _FINANCIAL_TITLE_MARKERS)
_PORTFOLIO_TITLE_MARKERS_NORM = tuple(normalize_fa(m) for m in _PORTFOLIO_TITLE_MARKERS)


def looks_like_financial_report(title: str | None) -> bool:
    norm = normalize_fa(title)
    return bool(norm) and any(m in norm for m in _FINANCIAL_TITLE_MARKERS_NORM)


def looks_like_portfolio_report(title: str | None) -> bool:
    norm = normalize_fa(title)
    return bool(norm) and any(m in norm for m in _PORTFOLIO_TITLE_MARKERS_NORM)


def _warn_newer_skipped(symbol: str, newer: list[dict[str, Any]], returned: int) -> None:
    """هشدارِ صریح وقتی اطلاعیه‌ی *جدیدتری* رد شده و گزارشِ قدیمی‌تری جایش برگشته است."""
    if not newer or not returned:
        return
    detail = "؛ ".join(f"TracingNo={e['tracing_no']} ({e['reason']})" for e in newer[:5])
    logger.warning(
        "%s: %d اطلاعیه‌ی جدیدتر رد شد و گزارشِ قدیمی‌تری برگشت -- آخرین وضعیت ممکن است در خروجی نباشد: %s",
        symbol, len(newer), detail,
    )


def _extract_all_cells(ds: dict) -> list[dict]:
    cells: list[dict] = []
    for sheet in get_ci(ds, "sheets") or []:
        for table in get_ci(sheet, "tables") or []:
            cells.extend(get_ci(table, "cells") or [])
    return cells


def fetch_financial_reports(
    symbol: str,
    *,
    sheet_id: str | Callable[[str], str],
    from_date: str | None = None,
    to_date: str | None = None,
    audited: bool | None = None,
    max_reports: int | None = None,
    progress: bool | ProgressCallback | None = True,
    client: Any | None = None,
    use_cache: bool = True,
    max_concurrency: int = 1,
    store: Any | None = None,
    skipped: list[dict[str, Any]] | None = None,
) -> list[FinancialReport]:
    """
    برایِ هر اطلاعیه‌یِ صورتِ مالیِ نماد (بعدِ فیلترِ عنوان)، دیتاسورس رو می‌گیره.

    sheet_id: یا یه رشته‌ی ثابت، یا تابعی که عنوانِ letter رو می‌گیره و
    sheet_id مناسب رو برمی‌گردونه -- برایِ وقتی که sheet_id بینِ نسخه‌ی
    تلفیقی/غیرتلفیقیِ letter فرق می‌کنه (نگاه کن به cash_flow_statement).

    max_reports: اگه بدی (مثلاً ۱)، فقط همین‌تعداد گزارشِ *موفق* رو می‌گیره و
    بقیه‌ی اطلاعیه‌هایِ تاریخی رو اصلاً درخواست نمی‌زنه -- برایِ کارِ اکتشافی/
    تستِ یه sheet_idِ جدید (که نیازی به کلِ تاریخچه نیست) خیلی به‌صرفه‌تره؛
    وگرنه پیش‌فرض کلِ تاریخچه رو می‌گیره (ممکنه ده‌ها درخواست بشه).

    skipped: اگر لیستی بدهی، هر اطلاعیه‌ای که *جدیدتر* از گزارشِ برگشتی بوده و رد شده،
    با دلیلش به آن اضافه می‌شود:
      "network"       خطای شبکه؛ گزارشِ قدیمی‌تر دیگر قابل اعتماد نیست
      "no_datasource" بدونِ داده‌ی ساختاریافته (یا علامتِ انبار)
      "rejected"      محتوا با گاردِ fallback نخواند
    بدونِ این لیست، رد شدن‌ها بی‌صدا بود و یک گزارشِ قدیمی‌تر جای گزارشِ جدید می‌نشست.
    """
    if max_concurrency > 1 and max_reports is None:
        return fetch_financial_reports_parallel(
            symbol,
            sheet_id=sheet_id,
            from_date=from_date,
            to_date=to_date,
            audited=audited,
            max_reports=max_reports,
            progress=progress,
            client=client,
            use_cache=use_cache,
            max_concurrency=max_concurrency,
            store=store,
            skipped=skipped,
        )

    reports: list[FinancialReport] = []
    progress_callback = make_progress(progress, label=f"گزارش {symbol}")
    local_skipped: list[dict[str, Any]] = []
    order = -1  # جایگاهِ اطلاعیه بینِ اطلاعیه‌های صورتِ مالیِ همین جست‌وجو (۰ = جدیدترین)

    def _skip(letter: Any, reason: str, detail: str) -> None:
        entry = {"tracing_no": letter.tracing_no, "title": letter.title or "",
                 "reason": reason, "detail": detail, "order": order}
        local_skipped.append(entry)
        if skipped is not None:
            skipped.append(entry)

    for letter in search_letters(
        symbol,
        from_date=from_date,
        to_date=to_date,
        category=CATEGORY_ANNUAL_FINANCIAL_STATEMENTS,
        letter_type=LETTER_TYPE_INTERIM_FINANCIAL_STATEMENTS,
        audited=audited,
        progress=progress_callback,
        client=client,
        use_cache=use_cache,
        raise_if_empty=False,
    ):
        if max_reports is not None and len(reports) >= max_reports:
            break
        if not looks_like_financial_report(letter.title):
            continue
        order += 1

        title = letter.title or ""
        resolved_sheet_id = sheet_id(title) if callable(sheet_id) else sheet_id

        if progress_callback:
            progress_callback(
                SearchProgress(
                    phase="download",
                    page=0,
                    page_count=1,
                    scanned=len(reports),
                    yielded=len(reports),
                    current_title=letter.title,
                    current_date=letter.publish_date_time,
                )
            )
        # آرگومان‌های اختیاری فقط وقتی فرستاده می‌شوند که غیرپیش‌فرض باشند (سازگاری با فراخوان‌های قبلی).
        ds_kwargs: dict[str, Any] = {"sheet_id": resolved_sheet_id}
        if client is not None or not use_cache:
            ds_kwargs.update(client=client, use_cache=use_cache)
        if store is not None:
            ds_kwargs["store"] = store
        try:
            ds = get_datasource(letter, **ds_kwargs)
        except DataSourceError as exc:
            # Many Codal notices are valid HTML/PDF/Excel disclosures without
            # an embedded datasource.  This is expected filtering, not a
            # request failure, so keep it out of normal warning output.
            logger.debug("اطلاعیه بدون datasource رد شد (TracingNo=%s): %s", letter.tracing_no, exc)
            _skip(letter, "no_datasource", str(exc))
            continue
        except RateLimitError:
            raise
        except NetworkError as exc:
            logger.warning("خطای شبکه برای گزارش (TracingNo=%s): %s", letter.tracing_no, exc)
            _skip(letter, "network", str(exc))
            continue

        if progress_callback:
            progress_callback(
                SearchProgress(
                    phase="download",
                    page=1,
                    page_count=1,
                    scanned=len(reports),
                    yielded=len(reports),
                    current_title=letter.title,
                    current_date=get_ci(ds, "periodEndToDate") or letter.publish_date_time,
                )
            )

        cells = _extract_all_cells(ds)

        # گاردِ ضدِ fallbackِ بی‌سروصدا -- همون منطقی که قبلاً فقط تویِ
        # fetch_structured_sheet/auditor_opinion_text بود، اینجا هم لازمه؛
        # این دقیقاً همون باگی بود که cash_flow_statement رو خراب کرده بود.
        # برایِ خودِ صورتِ سود و زیان (چه غیرتلفیقی چه تلفیقی) رد می‌شیم چون
        # محتوایِ واقعیِ خودش طبیعتاً همین امضا رو داره.
        if resolved_sheet_id not in _INCOME_STATEMENT_SHEET_IDS:
            reason = _fallback_reason(ds, resolved_sheet_id, cells)
            if reason:
                logger.warning(
                    "رد شد (TracingNo=%s، sheet_id=%s): %s -- رد می‌شه.",
                    letter.tracing_no, resolved_sheet_id, reason,
                )
                _skip(letter, "rejected", reason)
                continue

        row_df = cells_to_dataframe(cells)

        reports.append(
            FinancialReport(
                tracing_no=letter.tracing_no,
                symbol=letter.symbol,
                company_name=letter.company_name,
                title=letter.title,
                sent_date_time=letter.sent_date_time,
                publish_date_time=letter.publish_date_time,
                is_audited=get_ci(ds, "isAudited"),
                is_consolidated=is_consolidated_title(title),
                period_end_to_date=get_ci(ds, "periodEndToDate"),
                year_end_to_date=get_ci(ds, "yearEndToDate"),
                row_code_df=row_df,
                raw=ds,
            )
        )
        if len(reports) == 1:
            _warn_newer_skipped(symbol, [e for e in local_skipped if e["order"] < order], 1)
        if progress_callback and max_reports is not None and len(reports) >= max_reports:
            progress_callback(
                SearchProgress(
                    phase="report",
                    page=0,
                    page_count=1,
                    scanned=len(reports),
                    yielded=len(reports),
                    current_title=letter.title,
                    current_date=get_ci(ds, "periodEndToDate"),
                    done=True,
                )
            )
    return reports


def fetch_financial_reports_parallel(
    symbol: str,
    *,
    sheet_id: str | Callable[[str], str],
    from_date: str | None = None,
    to_date: str | None = None,
    audited: bool | None = None,
    max_reports: int | None = None,
    progress: bool | ProgressCallback | None = True,
    client: Any | None = None,
    use_cache: bool = True,
    max_concurrency: int = 5,
    store: Any | None = None,
    skipped: list[dict[str, Any]] | None = None,
) -> list[FinancialReport]:
    """Download multiple datasource pages concurrently with bounded workers.

    skipped: مثل fetch_financial_reports -- اطلاعیه‌های ردشده با دلیل (network / no_datasource / rejected)
    و جایگاهشان (order) اضافه می‌شوند.
    """
    letters = [
        letter for letter in search_letters(
            symbol,
            from_date=from_date,
            to_date=to_date,
            category=CATEGORY_ANNUAL_FINANCIAL_STATEMENTS,
            letter_type=LETTER_TYPE_INTERIM_FINANCIAL_STATEMENTS,
            audited=audited,
            progress=progress,
            client=client,
            use_cache=use_cache,
            raise_if_empty=False,
        )
        if looks_like_financial_report(letter.title)
    ]

    local_skipped: list[dict[str, Any]] = []

    def _skip(index: int, letter: Any, reason: str, detail: str) -> None:
        entry = {"tracing_no": letter.tracing_no, "title": letter.title or "",
                 "reason": reason, "detail": detail, "order": index}
        local_skipped.append(entry)  # list.append زیرِ GIL اتمی است؛ ترتیب بعداً با order مرتب می‌شود
        if skipped is not None:
            skipped.append(entry)

    rate_limited: list[RateLimitError] = []  # اولین 429: بقیه‌ی اطلاعیه‌ها دیگر درخواست نمی‌شوند

    def fetch_one(index: int, letter: Any) -> FinancialReport | None:
        if rate_limited:
            return None
        resolved = sheet_id(letter.title or "") if callable(sheet_id) else sheet_id
        try:
            ds = get_datasource(letter, sheet_id=resolved, client=client, use_cache=use_cache, store=store)
        except DataSourceError as exc:
            logger.debug("گزارش موازی بدون datasource رد شد (TracingNo=%s): %s", letter.tracing_no, exc)
            _skip(index, letter, "no_datasource", str(exc))
            return None
        except RateLimitError as exc:
            rate_limited.append(exc)
            return None
        except NetworkError as exc:
            logger.warning("خطای شبکه برای گزارش (TracingNo=%s): %s", letter.tracing_no, exc)
            _skip(index, letter, "network", str(exc))
            return None
        cells = _extract_all_cells(ds)
        if resolved not in _INCOME_STATEMENT_SHEET_IDS:
            reason = _fallback_reason(ds, resolved, cells)
            if reason:
                logger.warning("رد شد (TracingNo=%s، sheet_id=%s): %s", letter.tracing_no, resolved, reason)
                _skip(index, letter, "rejected", reason)
                return None
        return FinancialReport(
            tracing_no=letter.tracing_no,
            symbol=letter.symbol,
            company_name=letter.company_name,
            title=letter.title,
            sent_date_time=letter.sent_date_time,
            publish_date_time=letter.publish_date_time,
            is_audited=get_ci(ds, "isAudited"),
            is_consolidated=is_consolidated_title(letter.title),
            period_end_to_date=get_ci(ds, "periodEndToDate"),
            year_end_to_date=get_ci(ds, "yearEndToDate"),
            row_code_df=cells_to_dataframe(cells),
            raw=ds,
        )

    results: dict[int, FinancialReport] = {}
    with ThreadPoolExecutor(max_workers=max(1, max_concurrency)) as executor:
        futures = {executor.submit(fetch_one, index, letter): index for index, letter in enumerate(letters)}
        for future in as_completed(futures):
            report = future.result()
            if report is not None:
                results[futures[future]] = report

    if rate_limited:
        raise rate_limited[0]  # کدال محدودیتِ نرخ داده؛ ادامه دادن فقط بلاک را طولانی‌تر می‌کند
    ordered = [results[index] for index in sorted(results)]
    if results:
        first = min(results)
        _warn_newer_skipped(symbol, sorted((e for e in local_skipped if e["order"] < first),
                                           key=lambda e: e["order"]), len(ordered))
    return ordered if max_reports is None else ordered[:max_reports]


def income_statement(symbol: str, **kwargs: Any) -> list[FinancialReport]:
    """
    صورتِ سود و زیان -- خودکار بینِ نسخه‌ی تلفیقی (sheet_id=13) و غیرتلفیقی
    (sheet_id=1) بر اساسِ عنوانِ letter انتخاب می‌کنه. قبلاً همیشه sheet_id=1
    (غیرتلفیقی) می‌گرفت، حتی برایِ letterهایِ «تلفیقی» -- یعنی is_consolidated
    درست ثبت می‌شد ولی خودِ ارقام واقعاً ارقامِ شرکتِ اصلی بودن، نه گروه.
    ⚠️ این یعنی خروجیِ این تابع برایِ letterهایِ تلفیقی از نسخه‌ی ۰.۸.۶ به
    بعد عوض شده (تصمیمِ آگاهانه، نه باگِ جدید).
    """
    return fetch_financial_reports(symbol, sheet_id=_income_statement_sheet_id, **kwargs)


def balance_sheet(symbol: str, **kwargs: Any) -> list[FinancialReport]:
    """
    ترازنامه -- خودکار بینِ نسخه‌ی تلفیقی (sheet_id=14) و غیرتلفیقی
    (sheet_id=0) بر اساسِ عنوانِ letter انتخاب می‌کنه. همون توضیحِ
    income_statement اینجا هم صدق می‌کنه.
    """
    return fetch_financial_reports(symbol, sheet_id=_balance_sheet_sheet_id, **kwargs)


def cash_flow_statement(symbol: str, **kwargs: Any) -> list[FinancialReport]:
    """
    صورتِ جریانِ وجوهِ نقد -- خودکار بینِ نسخه‌ی تلفیقی (sheet_id=15) و
    غیرتلفیقی (sheet_id=9) بر اساسِ عنوانِ letter انتخاب می‌کنه -- دقیقاً
    همون معیارِ «تلفیقی» in title که is_consolidated هم ازش استفاده می‌کنه.
    هر دو sheet_id با title_Fa/aliasNameِ خودِ کدال (diagnose_cash_flow_sheets.py)
    و probe_sheet_ids.py رویِ چند letterِ مستقلِ فملی تأیید شدن.
    """
    return fetch_financial_reports(symbol, sheet_id=_cash_flow_sheet_id, **kwargs)


def equity_changes_statement(symbol: str, **kwargs: Any) -> list[FinancialReport]:
    """صورت تغییرات حقوق مالکانه با انتخاب خودکار تلفیقی/غیرتلفیقی."""
    return fetch_financial_reports(symbol, sheet_id=_equity_changes_sheet_id, **kwargs)


def comprehensive_income_statement(symbol: str, **kwargs: Any) -> list[FinancialReport]:
    """صورت سود و زیان جامع با انتخاب خودکار تلفیقی/غیرتلفیقی."""
    return fetch_financial_reports(symbol, sheet_id=_comprehensive_income_sheet_id, **kwargs)


def fetch_structured_sheet(
    symbol: str,
    *,
    sheet_id: str,
    from_date: str | None = None,
    to_date: str | None = None,
    header_row: int | None = 1,
    max_reports: int | None = None,
) -> list[PortfolioReport]:
    """
    برایِ sheetهایِ لیستی/جدولیِ عمومی (شرکت‌هایِ فرعی/وابسته، اعضایِ هیئتِ
    مدیره، ...) که سرفصلِ ثابتِ row_code ندارن -- برخلافِ fund_portfolio از
    خودِ `var datasource` می‌خونه (نه اکسل)، چون این‌ها بخشِ همون اطلاعیه‌ی
    صورتِ مالی‌ان که datasource داره.

    max_reports: نگاه کن به توضیحاتِ fetch_financial_reports -- همون منطق.
    """
    reports: list[PortfolioReport] = []
    for letter in search_letters(
        symbol,
        from_date=from_date,
        to_date=to_date,
        category=CATEGORY_ANNUAL_FINANCIAL_STATEMENTS,
        letter_type=LETTER_TYPE_INTERIM_FINANCIAL_STATEMENTS,
        raise_if_empty=False,
    ):
        if max_reports is not None and len(reports) >= max_reports:
            break
        if not looks_like_financial_report(letter.title):
            continue
        try:
            ds = get_datasource(letter, sheet_id=sheet_id)
        except (DataSourceError, NetworkError) as exc:
            logger.warning("رد شد (TracingNo=%s): %s", letter.tracing_no, exc)
            continue

        grid_df = cells_to_grid(_extract_all_cells(ds), header_row=header_row)

        # ⚠️ عمداً از grid_df.astype(str) استفاده نمی‌کنیم: تویِ pandas>=2.x
        # (رویِ pandas 3.0.x تأییدشده)، astype(str) مقادیرِ None/NaN رو به
        # رشته‌یِ "nan" تبدیل نمی‌کنه -- به‌صورتِ float خام نگه‌شون می‌داره،
        # حتی وقتی dtypeِ ستون به‌ظاهر "str" گزارش می‌شه. این باعث می‌شد
        # " ".join(...) رویِ ردیف‌هایی که سلولِ خالی/None داشتن (دقیقاً
        # همینایی که این تابع دنبالشونه) با
        # TypeError: sequence item N: expected str instance, float found بترکه.
        # str() ساخته‌شده‌ی خودِ پایتون رویِ هر مقدار، برخلافِ astype(str)، همیشه
        # NaN رو درست تبدیل می‌کنه.
        if not grid_df.empty and _looks_like_income_statement_fallback(
            " ".join(str(v) for v in grid_df.values.flatten())
        ):
            logger.warning(
                "رد شد (TracingNo=%s، sheet_id=%s): این sheet_id بی‌سروصدا محتوایِ صورتِ سود و زیان "
                "برگردوند (نه چیزی که ازش خواسته شده بود) -- به‌جایِ برگردوندنِ داده‌یِ اشتباه، "
                "رد می‌شه. این sheet_id هنوز تأیید نشده؛ با diagnose_sheets.py بررسیش کن.",
                letter.tracing_no, sheet_id,
            )
            continue

        reports.append(
            PortfolioReport(
                tracing_no=letter.tracing_no,
                symbol=letter.symbol,
                company_name=letter.company_name,
                title=letter.title,
                sent_date_time=letter.sent_date_time,
                publish_date_time=letter.publish_date_time,
                period_end_to_date=get_ci(ds, "periodEndToDate"),
                grid_df=grid_df,
                raw=ds,
            )
        )
    return reports


def fetch_excel_sheet(
    symbol: str,
    *,
    sheet_name: str,
    from_date: str | None = None,
    to_date: str | None = None,
    audited: bool | None = None,
    max_reports: int | None = None,
    max_scan: int | None = DEFAULT_MAX_ATTACHMENT_SCAN,
    clean: bool = True,
    progress: bool | ProgressCallback | None = True,
    use_cache: bool = True,
) -> list[PortfolioReport]:
    """
    برایِ sheetهایی که اصلاً تویِ datasourceِ JSON نیستن (شرکت‌هایِ فرعی/
    وابسته، اعضایِ هیئتِ‌مدیره -- با diagnose_sheets.py/probe_sheet_ids.py
    رویِ فملی تأیید شد که هیچ sheet_idِ عددی‌ای این‌ها رو برنمی‌گردونه، فقط
    fallback به صورتِ سود و زیان می‌خوره یا DataSourceError می‌ده): به‌جایِ
    `get_datasource`، ضمیمه‌ی اکسلِ خودِ letter رو می‌گیره (`fetch_attachment_tables`)
    و دنبالِ شیتی می‌گرده که اسمش، بعدِ `normalize_fa`، *دقیقاً* با sheet_name
    یکی باشه.

    ⚠️ عمداً تطابقِ جزئی/substring پشتیبانی نمی‌شه: مثلاً «صورت جریان های
    نقدی» substringِ «صورت جریان های نقدی تلفیقی»ه، و قبولِ تطابقِ جزئی
    باعث می‌شه بی‌سروصدا نسخه‌ی اشتباه (غیرتلفیقی به‌جایِ تلفیقی) برگرده --
    دقیقاً همون کلاس‌باگی که این پروژه چندبار (sheet_id=19، sheet_id=15)
    باهاش دست‌به‌گریبان بود.

    live-testِ زنده (نه mock) رویِ خزر تأیید کرد این روش واقعاً کار می‌کنه
    (شرکت‌هایِ فرعی/وابسته/اعضایِ هیئت‌مدیره، هر سه با جدولِ کامل برگشتن).
    رویِ فملی خروجیِ شرکت‌هایِ فرعی/وابسته خالیه -- چون فملی اصلاً چنین
    شرکتی برایِ افشا نداره، نه چون تابع خراب باشه.
    """
    target_norm = normalize_fa(sheet_name)
    reports: list[PortfolioReport] = []
    scanned_attachments = 0
    progress_callback = make_progress(progress, label=f"ضمیمه {sheet_name} {symbol}")
    for letter in search_letters(
        symbol,
        from_date=from_date,
        to_date=to_date,
        category=CATEGORY_ANNUAL_FINANCIAL_STATEMENTS,
        letter_type=LETTER_TYPE_INTERIM_FINANCIAL_STATEMENTS,
        audited=audited,
        progress=progress_callback,
        raise_if_empty=False,
    ):
        if max_reports is not None and len(reports) >= max_reports:
            break
        if not looks_like_financial_report(letter.title):
            continue
        # سقفِ تعدادِ ضمیمه‌هایِ دانلودشده: هر دانلودِ اکسل چندثانیه طول می‌کشه و
        # اگه شیتِ موردنظر تویِ اطلاعیه‌هایِ اخیر نباشه، بدونِ سقف کلِ تاریخچه
        # (تا سال‌هایِ ۱۳۹۹ و قدیمی‌تر) دانلود می‌شد و دقایق طول می‌کشید.
        if max_scan is not None and scanned_attachments >= max_scan:
            logger.info(
                "«%s» برایِ %s تویِ %d ضمیمه‌یِ اخیر پیدا نشد؛ جست‌وجو متوقف شد "
                "(برایِ گشتنِ بیشتر max_scan رو بالاتر بده یا None بذار).",
                sheet_name, symbol, max_scan,
            )
            break
        scanned_attachments += 1

        try:
            if use_cache:
                sheets = fetch_attachment_tables(letter)
            else:
                sheets = fetch_attachment_tables(letter, use_cache=False)
        except (ExcelParseError, NetworkError) as exc:
            logger.warning("رد شد (TracingNo=%s): %s", letter.tracing_no, exc)
            continue

        matched_df = None
        for sheet_title, df in sheets.items():
            if normalize_fa(sheet_title) == target_norm:
                matched_df = df
                break
        if matched_df is None:
            continue

        quality = None
        if clean:
            matched_df, quality = clean_table(matched_df)
            quality.source = "attachment_html_or_excel"
            quality.sheet_name = sheet_title
            quality.sheet_name_matched_exactly = True

        reports.append(
            PortfolioReport(
                tracing_no=letter.tracing_no,
                symbol=letter.symbol,
                company_name=letter.company_name,
                title=letter.title,
                sent_date_time=letter.sent_date_time,
                publish_date_time=letter.publish_date_time,
                period_end_to_date=None,  # اکسل این متادیتا رو نداره -- فقط JSON datasource داره
                grid_df=matched_df,
                raw={},
                quality=quality,
            )
        )
    return reports


def subsidiary_companies(symbol: str, **kwargs: Any) -> list[PortfolioReport]:
    """
    لیستِ شرکت‌هایِ فرعی -- از ضمیمه‌یِ اکسلِ خودِ اطلاعیه می‌خونه (نگاه کن
    به توضیحاتِ fetch_excel_sheet). با تستِ زنده رویِ خزر تأیید شد.
    ⚠️ اگه نماد اصلاً شرکتِ فرعی‌ای نداشته باشه (مثلِ فملی)، خروجی طبیعتاً
    خالیه -- یعنی داده‌ای نیست، نه اینکه تابع خراب باشه.
    """
    return fetch_excel_sheet(symbol, sheet_name="شرکت های فرعی", **kwargs)


def affiliated_companies(symbol: str, **kwargs: Any) -> list[PortfolioReport]:
    """لیستِ شرکت‌هایِ وابسته -- همون روشِ subsidiary_companies."""
    return fetch_excel_sheet(symbol, sheet_name="شرکت های وابسته", **kwargs)


def board_members(symbol: str, **kwargs: Any) -> list[PortfolioReport]:
    """لیستِ اعضایِ هیئتِ مدیره -- همون روشِ subsidiary_companies. با تستِ زنده رویِ فملی و خزر هر دو تأیید شد."""
    return fetch_excel_sheet(symbol, sheet_name="اعضای هیئت مدیره", **kwargs)


def _first_or_raise(kind: str, symbol: str, reports: list) -> Any:
    if not reports:
        raise DataSourceError(f"هیچ {kind} قابل‌استفاده‌ای برای {symbol!r} پیدا نشد.")
    return reports[0]


def latest_income(symbol: str, **kwargs: Any) -> FinancialReport:
    kwargs["max_reports"] = 1
    return _first_or_raise("صورت سود و زیان", symbol, income_statement(symbol, **kwargs))


def latest_balance_sheet(symbol: str, **kwargs: Any) -> FinancialReport:
    kwargs["max_reports"] = 1
    return _first_or_raise("ترازنامه", symbol, balance_sheet(symbol, **kwargs))


def latest_cash_flow(symbol: str, **kwargs: Any) -> FinancialReport:
    kwargs["max_reports"] = 1
    return _first_or_raise("صورت جریان نقد", symbol, cash_flow_statement(symbol, **kwargs))


def list_subsidiaries(symbol: str, **kwargs: Any) -> list[PortfolioReport]:
    return subsidiary_companies(symbol, **kwargs)


def list_affiliates(symbol: str, **kwargs: Any) -> list[PortfolioReport]:
    return affiliated_companies(symbol, **kwargs)


def list_board_members(symbol: str, **kwargs: Any) -> list[PortfolioReport]:
    return board_members(symbol, **kwargs)


def compare_report_data(old: Any, new: Any, *, key: str | None = None) -> dict[str, Any]:
    return compare_reports(old, new, key=key)


def _optional_ownership(function: Callable[..., list], symbol: str) -> list:
    """بخشِ اختیاریِ مالکیت: خطا فقط لاگ می‌شود و جای آن خالی می‌ماند (کلِ snapshot نباید بمیرد)."""
    try:
        return function(symbol, max_reports=1)
    except CodalError as exc:
        logger.warning("بخش اختیاری %s برای %s رد شد: %s", function.__name__, symbol, exc)
        return []


def financial_snapshot(
    symbol: str, *, max_reports: int = 1, include_ownership: bool = False, **kwargs: Any
) -> list[FinancialSnapshot]:
    """
    برایِ هر دوره (تا max_reports تا)، صورت‌هایِ مالیِ اصلی رو با هم برمی‌گردونه.

    include_ownership=True: ساختارِ مالکیت/هیئت‌مدیره (subsidiary_companies/
    affiliated_companies/board_members) رو هم اضافه می‌کنه. این سه‌تا
    period-specific نیستن (همیشه جدیدترین وضعیتِ افشاشده رو نشون می‌دن، نه
    وضعیتِ تاریخیِ مربوط به هر دوره‌یِ مالی)، پس فقط یه‌بار (نه به‌ازایِ هر
    دوره‌ای که تویِ max_reports هست) گرفته می‌شن و رویِ همه‌یِ خروجی‌ها
    تکرار می‌شن -- هم سریع‌تره، هم دقیقاً همون داده رو نشون می‌ده.
    """
    base = fetch_company_snapshot(symbol, max_reports=max_reports, **kwargs)

    subsidiaries = affiliates = board = None
    if include_ownership:
        subs = _optional_ownership(subsidiary_companies, symbol)
        affs = _optional_ownership(affiliated_companies, symbol)
        board_list = _optional_ownership(board_members, symbol)
        subsidiaries = subs[0] if subs else None
        affiliates = affs[0] if affs else None
        board = board_list[0] if board_list else None

    out: list[FinancialSnapshot] = []
    for item in base:
        statements = {k: value for k, value in item.items()
                      if k not in {"tracing_no", "title", "sent_date_time", "period_end_to_date"}}
        out.append(
            FinancialSnapshot(
                tracing_no=item["tracing_no"],
                title=item["title"],
                period_end_to_date=item.get("period_end_to_date"),
                statements=statements,
                subsidiaries=subsidiaries,
                affiliates=affiliates,
                board_members=board,
            )
        )
    return out


def auditor_opinion_text(
    symbol: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    max_reports: int | None = None,
    max_pages: int | None = None,
    candidate_limit: int | None = None,
    progress: bool | ProgressCallback | None = False,
) -> list[dict]:
    """
    نظرِ حسابرس -- برخلافِ بقیه، این متنیه نه جدولی، پس row_code_df/grid_df
    معنی نداره. هر آیتمِ خروجی: {"tracing_no", "title", "period_end_to_date", "text"}
    که "text" حاصلِ به‌هم‌چسبوندنِ مقدارِ همه‌ی سلول‌ها به‌ترتیبه.

    منوی شیت‌های کدال (فولاد TracingNo=1574326، وبملت TracingNo=1278431) نشان داد شیت 19
    «نظر حسابرس» وجود دارد ولی datasource ندارد؛ خودِ صفحه HTML است. پس اول datasource امتحان
    می‌شود و اگر نبود، متن از HTMLِ همان صفحه خوانده می‌شود. کلیدِ "source" می‌گوید متن از
    کجا آمده ("datasource" یا "html").

    max_reports: نگاه کن به توضیحاتِ fetch_financial_reports -- همون منطق.
    بدونِ این، رویِ نمادهایی با تاریخچه‌ی طولانی می‌تونه از *همه‌ی* letterهایِ
    تاریخچه رد بشه -- تویِ یه تستِ زنده رویِ فملی همین باعثِ ۱۰ دقیقه طول
    کشیدن شد.
    """
    results: list[dict] = []
    for letter in search_letters(
        symbol,
        from_date=from_date,
        to_date=to_date,
        category=CATEGORY_ANNUAL_FINANCIAL_STATEMENTS,
        letter_type=LETTER_TYPE_INTERIM_FINANCIAL_STATEMENTS,
        audited=True,
        max_pages=max_pages,
        limit=candidate_limit,
        progress=progress,
        raise_if_empty=False,
    ):
        if max_reports is not None and len(results) >= max_reports:
            break
        if not looks_like_financial_report(letter.title):
            continue
        try:
            ds = get_datasource(letter, sheet_id=SHEET_ID_AUDITOR_OPINION)
        except DataSourceError:
            # شیت 19 در قالب‌های جدید (مثلاً فولاد و وبملت) datasource ندارد و خودِ صفحه HTML است؛
            # متن از همان صفحه خوانده می‌شود. پاسخ در کشِ http هست و درخواستِ دوم فرستاده نمی‌شود.
            try:
                page_html, _page_url = fetch_report_html(letter, sheet_id=SHEET_ID_AUDITOR_OPINION)
            except RateLimitError:
                raise
            except NetworkError as exc:
                logger.warning("رد شد (TracingNo=%s): %s", letter.tracing_no, exc)
                continue
            html_text = auditor_text_from_html(page_html)
            if not html_text:
                logger.info("رد شد (TracingNo=%s): صفحه‌ی نظر حسابرس متنی شبیه گزارش حسابرس نداشت.",
                            letter.tracing_no)
                continue
            results.append(
                {
                    "tracing_no": letter.tracing_no,
                    "title": letter.title,
                    "period_end_to_date": extract_period_end(letter.title),
                    "text": html_text,
                    "source": "html",
                }
            )
            continue
        except RateLimitError:
            raise
        except NetworkError as exc:
            logger.warning("رد شد (TracingNo=%s): %s", letter.tracing_no, exc)
            continue

        cells = _extract_all_cells(ds)
        text = "\n".join(str(get_ci(c, "value")) for c in cells if get_ci(c, "value"))

        if text and _looks_like_income_statement_fallback(text):
            logger.warning(
                "رد شد (TracingNo=%s): sheet_id=%s (نظرِ حسابرس) بی‌سروصدا محتوایِ صورتِ سود و زیان "
                "برگردوند -- رد می‌شه. توجه: این تابع فقط رویِ گزارش‌هایِ حسابرسی‌شده معنی داره؛ اگه "
                "letterِ انتخاب‌شده «حسابرسی‌نشده» بود، اصلاً نظرِ حسابرس نداره.",
                letter.tracing_no, SHEET_ID_AUDITOR_OPINION,
            )
            continue

        results.append(
            {
                "tracing_no": letter.tracing_no,
                "title": letter.title,
                "period_end_to_date": get_ci(ds, "periodEndToDate"),
                "text": text,
                "source": "datasource",
            }
        )
    return results


def fetch_company_snapshot(
    symbol: str,
    *,
    sheet_ids: dict[str, str | Callable[[str], str]] | str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    max_reports: int = 1,
    progress: bool | ProgressCallback | None = True,
    store: Any | None = None,
) -> list[dict]:
    """
    برایِ گرفتنِ چندتا صورتِ مالی با هم (مثلاً ترازنامه+سودوزیان+جریانِ نقد)،
    این تابع فقط یه‌بار search_letters می‌زنه و برایِ همون اطلاعیه(ها) همه‌ی
    sheetها رو پشتِ‌سرِهم می‌گیره -- به‌جایِ اینکه income_statement()،
    balance_sheet()، cash_flow_statement() رو جدا جدا صدا بزنی (که هرکدوم
    خودش یه search_letters و یه دورِ کاملِ حلقه‌زدن رویِ اطلاعیه‌هاست).

    sheet_ids:
      - دیکشنری {نامِ_دلخواه: sheet_id} برایِ انتخابِ دستی، یا
      - "full" برایِ همه‌ی پنج صورتِ اصلیِ row_code (ترازنامه، سودوزیان،
        سودوزیانِ جامع، جریانِ نقد، تغییراتِ حقوقِ مالکانه)، یا
      - None (پیش‌فرض) برایِ فقط ترازنامه+سودوزیان+جریانِ نقد.
    max_reports: چندتا اطلاعیه‌ی *موفق* (نه کلِ تاریخچه) پردازش بشه -- پیش‌فرض ۱
    (فقط آخرین اطلاعیه)، چون بیشترِ کاربردها فقط آخرین دوره رو لازم دارن.

    خروجی: لیستی از دیکشنری -- هرکدوم شاملِ tracing_no/title/period_end_to_date
    + یه DataFrame (یا None اگه اون sheet برایِ این اطلاعیه نبود) به‌ازایِ هر
    کلیدِ sheet_ids.
    """
    if sheet_ids is None:
        sheet_ids = {
            "balance_sheet": _balance_sheet_sheet_id,
            "income_statement": _income_statement_sheet_id,
            "cash_flow_statement": _cash_flow_sheet_id,
        }
    elif isinstance(sheet_ids, str):
        if sheet_ids != "full":
            raise ValueError(f"sheet_ids={sheet_ids!r} معتبر نیست؛ None، \"full\" یا یک dict از نام به sheet_id بده.")
        sheet_ids = FULL_STATEMENT_SHEET_IDS

    results: list[dict] = []
    progress_callback = make_progress(progress, label=f"snapshot {symbol}")
    for letter in search_letters(
        symbol,
        from_date=from_date,
        to_date=to_date,
        category=CATEGORY_ANNUAL_FINANCIAL_STATEMENTS,
        letter_type=LETTER_TYPE_INTERIM_FINANCIAL_STATEMENTS,
        progress=progress_callback,
        raise_if_empty=False,
    ):
        if len(results) >= max_reports:
            break
        if not looks_like_financial_report(letter.title):
            continue

        entry: dict[str, Any] = {
            "tracing_no": letter.tracing_no,
            "title": letter.title,
            "sent_date_time": letter.sent_date_time,
            "period_end_to_date": None,
        }
        any_success = False
        for name, sid in sheet_ids.items():
            resolved_sid = sid(letter.title or "") if callable(sid) else sid
            try:
                sid_kwargs: dict[str, Any] = {"sheet_id": resolved_sid}
                if store is not None:
                    sid_kwargs["store"] = store
                ds = get_datasource(letter, **sid_kwargs)
            except (DataSourceError, NetworkError) as exc:
                logger.warning("رد شد (TracingNo=%s, sheet=%s): %s", letter.tracing_no, name, exc)
                entry[name] = None
                continue

            cells = _extract_all_cells(ds)
            if resolved_sid not in _INCOME_STATEMENT_SHEET_IDS:
                reason = _fallback_reason(ds, resolved_sid, cells)
                if reason:
                    logger.warning(
                        "رد شد (TracingNo=%s, sheet=%s، sheet_id=%s): %s -- رد می‌شه.",
                        letter.tracing_no, name, resolved_sid, reason,
                    )
                    entry[name] = None
                    continue

            any_success = True
            entry["period_end_to_date"] = entry["period_end_to_date"] or get_ci(ds, "periodEndToDate")
            entry[name] = cells_to_dataframe(cells)

        if any_success:
            results.append(entry)
            if progress_callback and len(results) >= max_reports:
                progress_callback(
                    SearchProgress(
                        phase="snapshot",
                        page=0,
                        page_count=1,
                        scanned=len(results),
                        yielded=len(results),
                        current_title=letter.title,
                        current_date=entry["period_end_to_date"],
                        done=True,
                    )
                )

    return results


def fund_portfolio(
    symbol: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    header_row: int | None = 1,
    max_reports: int | None = None,
    audited: bool | None = None,
    consolidated: bool | None = None,
    progress: bool | ProgressCallback | None = True,
    use_cache: bool = True,
    checkpoint_path: str | Path | None = None,
) -> list[PortfolioReport]:
    """
    صورت‌وضعیتِ پورتفویِ صندوق -- ساختارش جدولِ لیستیه (هر ردیف یه دارایی)، نه
    سرفصلِ ثابتِ صورتِ مالی، پس با cells_to_grid پردازش می‌شه نه cells_to_dataframe.

    برخلافِ صورت‌هایِ مالی، صفحه‌ی گزارشِ پورتفوی `var datasource` نداره
    (تأییدشده -- ۱۷ از ۱۷ موردِ تست‌شده)، پس دیگه اصلاً درخواستِ اضافه برایِ
    اون امتحان نمی‌کنیم -- مستقیم می‌ریم سراغِ ضمیمه‌ی اکسلِ خودِ اطلاعیه
    (`excel_url`/`attachment_url` که از نتیجه‌ی search میاد).
    """
    reports: list[PortfolioReport] = []
    progress_callback = make_progress(progress, label=f"پورتفوی {symbol}")
    for letter in search_letters(
        symbol,
        from_date=from_date,
        to_date=to_date,
        letter_type=LETTER_TYPE_PORTFOLIO_POSITION,
        audited=audited,
        consolidated=consolidated,
        progress=progress_callback,
        raise_if_empty=False,
    ):
        if max_reports is not None and len(reports) >= max_reports:
            break
        if not looks_like_portfolio_report(letter.title):
            continue

        grid_df = None
        period_end_to_date = extract_period_end(letter.title)
        raw: dict = {}

        try:
            sheets = fetch_attachment_tables(letter, use_cache=use_cache)
        except (ExcelParseError, NetworkError) as exc:
            logger.warning("رد شد (TracingNo=%s): %s", letter.tracing_no, exc)
            continue
        # شیتی را انتخاب کن که نام/هدرهایش با پورتفو سازگار است؛ ترتیب فایل قابل اتکا نیست.
        portfolio_tokens = ("پورتفو", "پرتفوی", "سرمایهگذاری", "سرمایه گذاری", "portfolio")
        candidates = [(name, df) for name, df in sheets.items() if not df.empty]
        named = [(name, df) for name, df in candidates
                 if any(token in normalize_fa(str(name)).lower() for token in portfolio_tokens)]
        grid_df = (named[0][1] if named else None)
        if grid_df is None:
            for _, df in candidates:
                blob = " ".join(str(v) for v in df.astype(object).head(5).to_numpy().ravel())
                norm = normalize_fa(blob).lower()
                if any(token in norm for token in ("نماد", "درصد", "بهایتما")):
                    grid_df = df
                    break
        if grid_df is None:
            grid_df = candidates[0][1] if candidates else None
        if grid_df is None:
            logger.warning("رد شد (TracingNo=%s): ضمیمه پارس شد ولی هیچ جدولی توش نبود.", letter.tracing_no)
            continue
        raw = {"sheets": {name: df.to_dict(orient="records") for name, df in sheets.items()}}

        reports.append(
            PortfolioReport(
                tracing_no=letter.tracing_no,
                symbol=letter.symbol,
                company_name=letter.company_name,
                title=letter.title,
                sent_date_time=letter.sent_date_time,
                publish_date_time=letter.publish_date_time,
                period_end_to_date=period_end_to_date,
                grid_df=grid_df,
                raw=raw,
            )
        )
        if checkpoint_path:
            checkpoint = Path(checkpoint_path)
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            temporary = checkpoint.with_suffix(checkpoint.suffix + ".tmp")
            temporary.write_text(json.dumps({"symbol": symbol, "completed": len(reports), "tracing_no": letter.tracing_no}, ensure_ascii=False), encoding="utf-8")
            temporary.replace(checkpoint)
    return reports


def monthly_activity(
    symbol: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    max_reports: int | None = None,
    progress: bool | ProgressCallback | None = True,
    client: Any | None = None,
    use_cache: bool = True,
    max_concurrency: int = 1,
    store: Any | None = None,
) -> list[FinancialReport]:
    """Return monthly-activity disclosures with the same typed report model.

    Monthly activity uses Codal category 3 and letter type 58, rather than the
    financial-statement filters.  The embedded datasource is parsed into the
    familiar ``FinancialReport`` shape; notices without a datasource are
    skipped because Codal also publishes PDF/HTML-only monthly notices.
    """
    reports: list[FinancialReport] = []
    progress_callback = make_progress(progress, label=f"فعالیت ماهانه {symbol}")
    for letter in search_letters(
        symbol,
        from_date=from_date,
        to_date=to_date,
        category=CATEGORY_MONTHLY_ACTIVITY,
        letter_type=LETTER_TYPE_MONTHLY_ACTIVITY,
        progress=progress_callback,
        client=client,
        use_cache=use_cache,
        raise_if_empty=False,
    ):
        if max_reports is not None and len(reports) >= max_reports:
            break
        try:
            ds = get_datasource(letter, client=client, use_cache=use_cache, store=store)
        except DataSourceError as exc:
            logger.debug("فعالیت ماهانه بدون datasource رد شد (TracingNo=%s): %s", letter.tracing_no, exc)
            continue
        except RateLimitError:
            raise
        except NetworkError as exc:
            logger.warning("خطای شبکه برای فعالیت ماهانه (TracingNo=%s): %s", letter.tracing_no, exc)
            continue
        cells = _extract_all_cells(ds)
        reports.append(
            FinancialReport(
                tracing_no=letter.tracing_no,
                symbol=letter.symbol,
                company_name=letter.company_name,
                title=letter.title,
                sent_date_time=letter.sent_date_time,
                publish_date_time=letter.publish_date_time,
                is_audited=get_ci(ds, "isAudited"),
                is_consolidated=is_consolidated_title(letter.title),  # مثل بقیه‌ی توابع: از عنوان، نه از کلیدِ اختیاریِ datasource
                period_end_to_date=get_ci(ds, "periodEndToDate"),
                year_end_to_date=get_ci(ds, "yearEndToDate"),
                row_code_df=cells_to_dataframe(cells),
                raw=ds,
            )
        )
    return reports
