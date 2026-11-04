"""سبک‌وزن‌ترین مدل‌های دامنه‌ی alilnvest_codal."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

BASE_URL = "https://www.codal.ir"


def _full_url(value: str | None) -> str | None:
    """اگه value یه مسیرِ نسبی بود (با / شروع می‌شد)، BASE_URL رو جلوش می‌چسبونه."""
    if not value:
        return value
    if value.startswith(("http://", "https://")):
        return value
    if value.startswith("/"):
        return f"{BASE_URL}{value}"
    return value


@dataclass
class Letter:
    """یه اطلاعیه‌یِ خام از نتیجه‌یِ search_letters."""

    tracing_no: int
    symbol: str
    company_name: str
    title: str
    letter_code: str
    sent_date_time: str
    publish_date_time: str
    has_html: bool
    has_excel: bool
    has_pdf: bool
    has_xbrl: bool
    has_attachment: bool
    url: str
    attachment_url: str
    pdf_url: str
    excel_url: str
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def report_url(self) -> str:
        return self.url


@dataclass
class DataSourceResult:
    """نتیجه‌ی مستقل یک نامه؛ خطای یک گزارش کل batch را متوقف نمی‌کند."""

    status: str
    letter: Letter
    data: dict[str, Any] | None = None
    error: str | None = None
    sheet_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "success" and self.data is not None

    @classmethod
    def from_raw(cls, raw: dict) -> "Letter":
        from .utils import get_ci, to_bool

        return cls(
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
            url=_full_url(get_ci(raw, "Url")),
            attachment_url=_full_url(get_ci(raw, "AttachmentUrl")),
            pdf_url=_full_url(get_ci(raw, "PdfUrl")),
            excel_url=_full_url(get_ci(raw, "ExcelUrl")),
            raw=raw,
        )


@dataclass
class ReportQuality:
    """
    گزارشِ شفافِ اینکه clean_table چیکار کرد -- تا کاربر بدونه دقیقاً چقدر
    از داده‌یِ خام دست‌نخورده مونده و چقدر تمیزکاری (حذفِ ردیف/ستونِ تکراری)
    روش انجام شده، به‌جایِ اینکه کورکورانه به خروجی اعتماد کنه.
    """

    source: str
    sheet_name: str | None = None
    sheet_name_matched_exactly: bool | None = None
    duplicate_rows_removed: int = 0
    empty_columns_removed: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class SubsidiaryCompany:
    """یه ردیفِ استانداردشده از شیتِ «شرکت های فرعی»/«شرکت های وابسته» -- نگاه کن به analytics.subsidiary_models."""

    symbol: str | None = None
    name: str | None = None
    registered_symbol: str | None = None
    registration_status: str | None = None
    activity_type: str | None = None
    auditor: str | None = None
    fiscal_year: str | None = None
    active_status: str | None = None
    status: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class BoardMember:
    """یه ردیفِ استانداردشده از شیتِ «اعضای هیئت مدیره» -- نگاه کن به analytics.board_models."""

    name: str | None = None
    role: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class FinancialReport:
    """خروجیِ income_statement/balance_sheet/cash_flow_statement/... -- یه صورتِ مالیِ row_code-indexed."""

    tracing_no: int
    symbol: str
    company_name: str
    title: str
    sent_date_time: str
    publish_date_time: str
    is_audited: bool | None
    is_consolidated: bool
    period_end_to_date: str | None
    year_end_to_date: str | None
    row_code_df: Any
    raw: dict = field(repr=False)
    quality: ReportQuality | None = None

    def to_json(self, path: str, **kwargs: Any) -> None:
        from .analytics import dataframe_to_json

        dataframe_to_json(
            self.row_code_df, path,
            metadata={"tracing_no": self.tracing_no, "symbol": self.symbol},
            **kwargs,
        )

    def to_excel(self, path: str, **kwargs: Any) -> None:
        self.row_code_df.to_excel(path, **kwargs)

    def to_csv(self, path: str, **kwargs: Any) -> None:
        kwargs.setdefault("encoding", "utf-8-sig")
        self.row_code_df.to_csv(path, **kwargs)


@dataclass
class PortfolioReport:
    """خروجیِ fund_portfolio/subsidiary_companies/affiliated_companies/board_members -- یه شبکه‌ی address-based، نه row_code."""

    tracing_no: int
    symbol: str
    company_name: str
    title: str
    sent_date_time: str
    publish_date_time: str
    period_end_to_date: str | None
    grid_df: Any
    raw: dict = field(repr=False)
    quality: ReportQuality | None = None

    def with_header(self, *, drop_header: bool = True) -> "PortfolioReport":
        """grid_df رو با clean_table تمیز می‌کنه (حذفِ ستونِ خالی/ردیفِ تکراری + تشخیصِ ردیفِ هدر) و quality رو پر می‌کنه."""
        from .analytics import clean_table

        self.grid_df, self.quality = clean_table(self.grid_df, drop_header=drop_header)
        return self

    def to_json(self, path: str, **kwargs: Any) -> None:
        from .analytics import dataframe_to_json

        dataframe_to_json(
            self.grid_df, path,
            metadata={"tracing_no": self.tracing_no, "symbol": self.symbol},
            **kwargs,
        )

    def to_excel(self, path: str, **kwargs: Any) -> None:
        kwargs.setdefault("index", False)
        self.grid_df.to_excel(path, **kwargs)

    def to_csv(self, path: str, **kwargs: Any) -> None:
        kwargs.setdefault("encoding", "utf-8-sig")
        kwargs.setdefault("index", False)
        self.grid_df.to_csv(path, **kwargs)

    @property
    def companies(self) -> list[SubsidiaryCompany]:
        """grid_df رو (بعدِ with_header) به لیستی از SubsidiaryCompany تبدیل می‌کنه."""
        from .analytics import subsidiary_models

        return subsidiary_models(self.grid_df)

    @property
    def board(self) -> list[BoardMember]:
        """grid_df رو (بعدِ with_header) به لیستی از BoardMember تبدیل می‌کنه."""
        from .analytics import board_models

        return board_models(self.grid_df)


@dataclass
class FinancialSnapshot:
    """خروجیِ financial_snapshot -- ترکیبِ چندتا صورتِ مالی + (اختیاری) ساختارِ مالکیت/هیئت‌مدیره برایِ یه دوره."""

    tracing_no: int
    title: str
    period_end_to_date: str | None
    statements: dict[str, Any]
    subsidiaries: PortfolioReport | None = None
    affiliates: PortfolioReport | None = None
    board_members: PortfolioReport | None = None

    @property
    def balance_sheet(self) -> Any:
        return self.statements.get("balance_sheet")

    @property
    def income_statement(self) -> Any:
        return self.statements.get("income_statement")

    @property
    def cash_flow(self) -> Any:
        return self.statements.get("cash_flow_statement")
