"""
استثناهایِ کتابخانه -- همه از CodalError ارث می‌برن تا با except CodalError
بشه همه رو گرفت.

هرکدوم، علاوه‌بر پیامِ فارسیِ خوانا برایِ لاگ/چاپ، فیلدهایِ ساخت‌یافته هم دارن
(url، tracing_no، symbol، status_code) تا کدِ فراخوان (مثلِ bourse_bot) بتونه
به‌جایِ پارس‌کردنِ متنِ فارسیِ پیام، برنامه‌نویسی‌شده تصمیم بگیره -- مثلاً:

    try:
        cp.income_statement(symbol)
    except cp.DataSourceError as exc:
        log.warning("رد شد", tracing_no=exc.tracing_no, url=exc.url)
"""

from __future__ import annotations


class CodalError(Exception):
    """کلاسِ پایه‌ی همه‌ی خطاهایِ این کتابخانه."""


class SymbolNotFoundError(CodalError):
    """نماد جست‌وجو شد ولی هیچ اطلاعیه‌ای برایِ اون برنگشت."""

    def __init__(self, message: str, *, symbol: str | None = None) -> None:
        super().__init__(message)
        self.symbol = symbol


class DataSourceError(CodalError):
    """صفحه‌ی گزارش گرفته شد ولی متغیرِ datasource پیدا/پارس نشد."""

    def __init__(self, message: str, *, tracing_no: int | None = None, url: str | None = None) -> None:
        super().__init__(message)
        self.tracing_no = tracing_no
        self.url = url


class NetworkError(CodalError):
    """درخواستِ HTTP بعدِ همه‌یِ retry ها هم شکست خورد."""

    def __init__(
        self,
        message: str,
        *,
        url: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.url = url
        self.status_code = status_code


class ExcelParseError(CodalError):
    """ضمیمه‌ی اکسل/attachment دانلود شد ولی با هیچ روشی (اکسلِ واقعی یا HTML-جدولی) پارس نشد."""

    def __init__(self, message: str, *, tracing_no: int | None = None, url: str | None = None) -> None:
        super().__init__(message)
        self.tracing_no = tracing_no
        self.url = url


class RateLimitError(NetworkError):
    """کدال با HTTP 429 درخواست را محدود کرد و همه‌ی تلاش‌ها هم 429 گرفتند.

    زیرکلاسِ NetworkError است؛ پس کدی که NetworkError را می‌گیرد بدون تغییر کار می‌کند.
    retry_after: ثانیه‌ای که سرور گفته صبر کنیم (اگر گفته باشد).
    """

    def __init__(
        self,
        message: str,
        *,
        url: str | None = None,
        status_code: int | None = 429,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, url=url, status_code=status_code)
        self.retry_after = retry_after


CodalNetworkError = NetworkError
"""Modern name for the compatibility-layer network failure."""


class CodalNotFoundError(CodalError):
    """Requested symbol or Codal resource does not exist."""

    def __init__(self, message: str, *, symbol: str | None = None, url: str | None = None) -> None:
        super().__init__(message)
        self.symbol = symbol
        self.url = url


class CodalParsingError(DataSourceError):
    """Response content was present but could not be parsed."""
