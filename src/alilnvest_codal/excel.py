"""
دانلود و پارسِ ضمیمه‌ی اکسل -- برایِ مواردی که `datasource` توکار نیست
(مثلِ صورت‌وضعیتِ پورتفویِ صندوق، یا شیت‌هایی مثلِ شرکت‌هایِ فرعی/وابسته/
اعضایِ هیئت‌مدیره که اصلاً بخشِ JSON datasource نیستن -- نگاه کن به
CHANGELOG نسخه‌ی ۰.۸.۹). صورت‌هایِ مالیِ اصلی نیازی به این ماژول ندارن.

طبقِ تجربه‌یِ صورت‌هایِ مالی، کدال گاهی به‌جایِ xlsx واقعی یه HTML-با-پسوندِ-xls
می‌فرسته؛ این ماژول اول موتورهایِ معمولی رو امتحان می‌کنه، بعد اگه HTML بود
با BeautifulSoup(html.parser) می‌خونه.

⚠️ عمداً از pd.read_html برایِ حالتِ HTML استفاده نمی‌کنیم: با تستِ زنده
رویِ خزر تأیید شد که pd.read_html (که از lxml/html5libِ سخت‌گیرتر استفاده
می‌کنه) رویِ همین فایل‌هایِ HTML-یِ کدال گاهی هیچ جدولی پیدا نمی‌کنه
("No tables found")، درحالی‌که BeautifulSoup با فلگِ لنیانت‌ترِ
`html.parser`ِ خودِ پایتون بدونِ مشکل پارسش می‌کنه. علاوه‌براین، نسخه‌ی
قبلی همیشه شیت‌ها رو "Sheet1"/"Sheet2" اسم‌گذاری می‌کرد -- که برایِ
fetch_excel_sheet (که برایِ تطابقِ دقیق به اسمِ واقعیِ فارسیِ شیت نیاز
داره) کاملاً بی‌فایده بود. الان اسمِ واقعی از تگِ h3ِ قبلِ هر جدول خونده
می‌شه.
"""

from __future__ import annotations

import io

import pandas as pd
from bs4 import BeautifulSoup

from . import http
from .exceptions import ExcelParseError
from .models import Letter

_HTML_HINTS = (b"<html", b"<!doctype", b"<table", b"<h3", b"<div", b"<meta", b"<head", b"<body", b"<!--", b"<?xml")


def _looks_like_html(content: bytes) -> bool:
    """فایلِ HTML (حتی بدونِ <html>/<!doctype>) را تشخیص می‌دهد؛ فایلِ اکسلِ باینری هیچ‌وقت با '<' شروع نمی‌شود."""
    head = content[:512].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    return head.startswith(b"<") and any(hint in head for hint in _HTML_HINTS)


def download_bytes(url: str, *, use_cache: bool = True) -> bytes:
    """دانلودِ ضمیمه. کشِ مرکزیِ http (با سقفِ تعداد) کافی است؛ کشِ جدا و بی‌سقف نداریم."""
    return http.get(url, timeout=45, use_cache=use_cache).content


def _rows_to_dataframe(rows: list[list[str]]) -> pd.DataFrame:
    """
    لیستِ ردیف‌ها رو به DataFrame تبدیل می‌کنه -- بدونِ فرض کردنِ ردیفِ اول
    به‌عنوانِ هدر (چون rowspan/colspanِ جدول‌هایِ کدال گاهی ردیفِ اول رو
    به‌شکلِ نامنظم/تکراری درمیاره) و بدونِ نیاز به طولِ برابرِ همه‌ی
    ردیف‌ها (پد می‌کنه با None).
    """
    if not rows:
        return pd.DataFrame()
    width = max(len(r) for r in rows)
    padded = [r + [None] * (width - len(r)) for r in rows]
    padded = [[(value if value != "" else None) for value in row] for row in padded]
    # ردیفِ تکراری عمداً حذف نمی‌شود: در صورت‌های مالی ردیف‌های یکسان (مثلاً «۰») معنی‌دار هستند.
    # تمیزکاریِ تکراری‌ها صریح و با گزارشِ کیفیت در analytics.clean_table انجام می‌شود.
    df = pd.DataFrame(padded)
    return df.loc[:, df.notna().any(axis=0)]


def _extract_html_sheets(content: bytes) -> dict[str, pd.DataFrame]:
    """
    اسمِ واقعیِ هر شیت رو از تگِ h3ِ قبلِ جدول(هاش) می‌گیره. اگه هیچ h3ای
    نبود (مثلِ بعضی گزارش‌هایِ پورتفوی که ساختارِ ساده‌تری دارن)، شیت‌ها
    رو Sheet1/Sheet2/... اسم‌گذاری می‌کنه -- fund_portfolio به اسمِ خاصی
    نیاز نداره، فقط اولین شیتِ غیرخالی رو برمی‌داره.
    """
    soup = BeautifulSoup(content, "html.parser")
    sheets: dict[str, pd.DataFrame] = {}
    current_title: str | None = None
    unnamed_count = 0

    # Codal may emit nested tables. Read only leaf tables; otherwise the
    # parent table counts cells from the child table a second time.
    candidates: dict[str, list[pd.DataFrame]] = {}
    for element in soup.find_all(["h3", "table"]):
        if element.name == "h3":
            current_title = element.get_text(strip=True)
            continue
        # Skip wrappers that contain another table, but allow the actual leaf
        # table even when it is nested inside that wrapper.
        if element.find("table") is not None:
            continue
        title = current_title
        if title is None:
            unnamed_count += 1
            title = f"Sheet{unnamed_count}"
        rows: list[list[str]] = []
        for tr in element.find_all("tr"):
            cells = tr.find_all(["td", "th"], recursive=False)
            if cells:
                row = [c.get_text(strip=True) for c in cells]
                if any(row):
                    rows.append(row)
        if rows:
            candidates.setdefault(title, []).append(_rows_to_dataframe(rows))

    for title, tables in candidates.items():
        # The real data table normally has the largest non-empty payload.
        chosen = max(
            tables,
            key=lambda df: (int(df.notna().sum().sum()), df.shape[0], -df.shape[1]),
        )
        sheets[title] = chosen

    if not sheets:
        raise ExcelParseError("محتوا HTML بود ولی هیچ جدولی توش پیدا نشد.")
    return sheets


def parse_excel_bytes(content: bytes) -> dict[str, pd.DataFrame]:
    errors: list[str] = []
    for engine in ("openpyxl", "calamine", "xlrd"):
        try:
            return pd.read_excel(io.BytesIO(content), sheet_name=None, header=None, engine=engine)
        except ImportError:
            continue
        except Exception as exc:  # noqa: BLE001 -- می‌خوایم همه‌ی موتورها رو امتحان کنیم
            errors.append(f"{engine}: {exc}")

    if _looks_like_html(content):
        try:
            return _extract_html_sheets(content)
        except ExcelParseError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ExcelParseError(f"محتوا HTML بود ولی پارس نشد: {exc}") from exc

    raise ExcelParseError(
        f"فایل با هیچ موتورِ اکسلی خونده نشد. شروعِ محتوا: {content[:150]!r} -- خطاها: {'; '.join(errors)}"
    )


def fetch_attachment_tables(letter: Letter, *, use_cache: bool = True) -> dict[str, pd.DataFrame]:
    """اکسل/ضمیمه‌ی اطلاعیه رو دانلود و پارس می‌کنه؛ اگه لینکی نبود ExcelParseError می‌ده."""
    url = letter.excel_url or letter.attachment_url
    if not url:
        raise ExcelParseError(f"هیچ لینکِ اکسل/ضمیمه‌ای برایِ TracingNo={letter.tracing_no} نیست.", tracing_no=letter.tracing_no)
    content = download_bytes(url, use_cache=use_cache)
    try:
        return parse_excel_bytes(content)
    except ExcelParseError as exc:
        exc.tracing_no = letter.tracing_no
        exc.url = url
        raise
