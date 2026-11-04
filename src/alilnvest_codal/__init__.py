"""
Alilnvest-Codal
=========
کتابخانهٔ مستقل Alilnvest برای کدال -- زنده، بدون لیست ثابت نماد.

اصولِ طراحی (حاصلِ یه بررسیِ چندمرحله‌ای رویِ منابع پژوهشی قبلی و کتابخانه‌های مشابه):
  - نماد همیشه زنده روی search.codal.ir چک می‌شه -> نمادهای تازه (توسن) از روز اول کار می‌کنن.
  - صورت‌هایِ مالیِ اصلی از متغیرِ جاواسکریپتِ `datasource` توی صفحه‌ی گزارش خونده می‌شن، نه از دانلودِ اکسل.
  - چندتا sheet (شرکت‌هایِ فرعی/وابسته، اعضایِ هیئت‌مدیره) اصلاً تویِ اون
    datasource نیستن -- برایِ اون‌ها ضمیمه‌ی اکسلِ خودِ اطلاعیه با تطابقِ
    دقیقِ اسمِ شیت خونده می‌شه (fetch_excel_sheet؛ نگاه کن به CHANGELOG ۰.۸.۹).
  - سرفصل‌ها با row_code ایندکس می‌شن (پایدارتر از تطبیقِ متنی).
  - ردیف‌های توضیحیِ بی‌مقدار (زیرِ جدول) به‌صورتِ پیش‌فرض حذف می‌شن.
  - قبل از گرفتنِ datasource، عنوانِ اطلاعیه چک می‌شه تا وقتِ اطلاعیه‌های غیرمالی هدر نره.
"""

from ._version import __version__

BRAND = "Alilnvest"
DISTRIBUTION_NAME = "Alilnvest-Codal"
from .exceptions import CodalError, SymbolNotFoundError, DataSourceError, NetworkError, ExcelParseError, RateLimitError
from .http import reset_circuit_breaker, configure, clear_cache, safe_mode, fast_mode, symbol_pause, reset_symbol_pacing
from .letter_codes import LETTER_CODES, describe_letter_code
from .utils import validate_jdate
from .progress import ConsoleProgress, SearchProgress
from .models import Letter, DataSourceResult, FinancialReport, PortfolioReport, ReportQuality, SubsidiaryCompany, BoardMember, FinancialSnapshot
from .search import search_letters, get_letter, latest_letter
from .datasource import get_datasource, get_datasource_result, fetch_report_html
from .sheets import SheetOption, SheetContent, parse_sheet_menu, list_sheets, read_sheet
from .frames import cells_to_dataframe, cells_to_grid, apply_row_code_map
from .reports import (
    fetch_financial_reports,
    fetch_structured_sheet,
    fetch_excel_sheet,
    fetch_company_snapshot,
    FULL_STATEMENT_SHEET_IDS,
    income_statement,
    balance_sheet,
    monthly_activity,
    cash_flow_statement,
    equity_changes_statement,
    comprehensive_income_statement,
    subsidiary_companies,
    affiliated_companies,
    board_members,
    latest_income,
    latest_balance_sheet,
    latest_cash_flow,
    list_subsidiaries,
    list_affiliates,
    list_board_members,
    financial_snapshot,
    compare_report_data,
    auditor_opinion_text,
    fund_portfolio,
    looks_like_financial_report,
    looks_like_portfolio_report,
)

from .checks import CheckResult, analyze_statement, analyze_set, analyze_portfolio, statement_kind, cash_flow_method
from .audit import SymbolAudit, audit_symbol, audit_symbols, write_reports, detect_kind
__all__ = [
    "CheckResult", "analyze_statement", "analyze_set", "analyze_portfolio",
    "statement_kind", "cash_flow_method", "SymbolAudit", "audit_symbol", "audit_symbols",
    "write_reports", "detect_kind",
    "CodalError",
    "SymbolNotFoundError",
    "DataSourceError",
    "NetworkError",
    "ExcelParseError",
    "RateLimitError",
    "reset_circuit_breaker",
    "configure",
    "clear_cache",
    "safe_mode",
    "fast_mode",
    "symbol_pause",
    "reset_symbol_pacing",
    "LETTER_CODES",
    "describe_letter_code",
    "validate_jdate",
    "SearchProgress",
    "ConsoleProgress",
    "Letter",
    "DataSourceResult",
    "FinancialReport",
    "PortfolioReport",
    "ReportQuality",
    "SubsidiaryCompany",
    "BoardMember",
    "FinancialSnapshot",
    "search_letters",
    "get_letter",
    "latest_letter",
    "get_datasource",
    "get_datasource_result",
    "fetch_report_html",
    "SheetOption",
    "SheetContent",
    "parse_sheet_menu",
    "list_sheets",
    "read_sheet",
    "cells_to_dataframe",
    "cells_to_grid",
    "apply_row_code_map",
    "fetch_financial_reports",
    "fetch_structured_sheet",
    "fetch_excel_sheet",
    "fetch_company_snapshot",
    "FULL_STATEMENT_SHEET_IDS",
    "income_statement",
    "balance_sheet",
    "monthly_activity",
    "cash_flow_statement",
    "equity_changes_statement",
    "comprehensive_income_statement",
    "subsidiary_companies",
    "affiliated_companies",
    "board_members",
    "latest_income",
    "latest_balance_sheet",
    "latest_cash_flow",
    "list_subsidiaries",
    "list_affiliates",
    "list_board_members",
    "financial_snapshot",
    "compare_report_data",
    "auditor_opinion_text",
    "fund_portfolio",
    "looks_like_financial_report",
    "looks_like_portfolio_report",
    "__version__",
    "BRAND",
    "DISTRIBUTION_NAME",
]


# 1.0 additions; the imports above intentionally remain for 0.9 compatibility.
from .api import (
    async_balance_sheet,
    async_income_statement,
    balance_sheet as modern_balance_sheet,
    income_statement as modern_income_statement,
)
from .cache import Cache
from .config import CodalConfig, DEFAULT_CONFIG
from .endpoints import BROWSER_HEADERS, CodalEndpoint
from .http import CodalHTTPClient
from .exceptions import CodalNetworkError, CodalNotFoundError, CodalParsingError
from .codal import Codal
from .health import health_check
from .automation import CodalRun, CodalBot, OperationResult, fetch_since
from .store import SQLiteStore, StoredDatasource

# New names are aliases at the package boundary; the legacy names above retain
# their richer optional arguments and historical report types.
__all__ += [
    "CodalConfig", "DEFAULT_CONFIG", "Cache", "CodalEndpoint", "BROWSER_HEADERS",
    "CodalHTTPClient", "CodalNetworkError", "CodalNotFoundError", "CodalParsingError",
    "async_income_statement", "async_balance_sheet", "modern_income_statement", "modern_balance_sheet", "Codal", "health_check", "CodalRun", "CodalBot", "OperationResult", "fetch_since",
    "SQLiteStore", "StoredDatasource",
]
# The stable 1.0 API must win at the package boundary.  The underlying legacy
# implementation is still available as reports.income_statement and retains
# its historical optional arguments.
income_statement = modern_income_statement  # noqa: F811 — legacy name intentionally replaced at package boundary
balance_sheet = modern_balance_sheet  # noqa: F811 — legacy name intentionally replaced at package boundary
