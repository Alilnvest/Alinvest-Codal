"""فهرستِ شیت‌های یک اطلاعیه (منوی «ddlTable» کدال) و خواندنِ شیت‌هایی که datasource ندارند.

کدال در هر صفحه‌ی گزارش یک منوی کشویی دارد که همه‌ی شیت‌های همان اطلاعیه را با شناسه‌شان فهرست
می‌کند. این منو تنها منبعِ قابل‌اعتماد برای اینکه «این اطلاعیه کدام شیت‌ها را دارد» است؛ شناسه‌ها
بین قالب‌ها (تولیدی، بانکی، بیمه، ...) فرق دارند.

سه نکته که این ماژول درست می‌کند:

* HTMLِ منو در بعضی قالب‌ها (مثلاً بانکی، وبملت) تگِ ``</option>`` ندارد. html.parser هر گزینه را
  داخلِ گزینه‌ی قبلی تودرتو می‌کرد و متنِ هر گزینه همه‌ی گزینه‌های بعدی را هم می‌گرفت. اینجا گزینه‌ها
  با مرزِ ``<option`` بعدی جدا می‌شوند، نه با درختِ DOM.
* شناسه‌ی منو در قالب‌های مختلف ``ctl00_ddlTable`` یا ``ddlTable`` است؛ هر دو پذیرفته می‌شوند.
* شیت‌های متنی/جدولی مثل «نظر حسابرس» (19)، «اعضای هیئت مدیره» (30) و «شرکت‌های فرعی» (51)
  اصلاً ``var datasource`` ندارند و خودِ صفحه HTML است؛ ``read_sheet`` در این حالت جدول‌ها و متنِ
  صفحه را برمی‌گرداند و آن را خطا حساب نمی‌کند.
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from bs4 import BeautifulSoup

from .datasource import extract_datasource, fetch_report_html
from .exceptions import DataSourceError
from .models import Letter
from .utils import get_ci, normalize_alias, normalize_fa

__all__ = [
    "SheetOption",
    "SheetContent",
    "parse_sheet_menu",
    "list_sheets",
    "read_sheet",
    "extract_html_content",
    "auditor_text_from_html",
]

# id یا name منو؛ ctl00_ddlTable (قالب تولیدی) یا ddlTable (قالب بانکی) یا ctl00$ddlTable.
_SELECT_RE = re.compile(
    r"<select\b(?P<attrs>[^>]*\b(?:id|name)\s*=\s*['\"]?[^'\">\s]*ddlTable(?=['\"\s>])['\"]?[^>]*)>(?P<body>.*?)</select\s*>",
    re.IGNORECASE | re.DOTALL,
)
# هر گزینه تا شروعِ گزینه‌ی بعدی، یا </option>، یا پایانِ منو.
_OPTION_RE = re.compile(
    r"<option\b(?P<attrs>[^>]*)>(?P<text>.*?)(?=<option\b|</option\s*>|</select\s*>|$)",
    re.IGNORECASE | re.DOTALL,
)
_VALUE_RE = re.compile(r"\bvalue\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", re.IGNORECASE)
_SELECTED_RE = re.compile(r"\bselected\b", re.IGNORECASE)
_ATTR_RE = re.compile(r"\b(id|name)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# تگ‌هایی که متنشان بخشی از محتوای شیت نیست.
_NON_CONTENT_TAGS = ("script", "style", "noscript", "select", "option", "input", "button",
                     "head", "iframe", "svg", "img", "link", "meta", "nav", "footer")
# نشانه‌های شروعِ متنِ گزارشِ حسابرس، به ترتیبِ اولویت (بعد از normalize_fa).
_AUDITOR_START_MARKERS = tuple(normalize_fa(m) for m in (
    "گزارش حسابرس مستقل", "گزارش حسابرس", "گزارش بازرس قانونی", "به مجمع عمومی", "اظهارنظر",
))


@dataclass(frozen=True)
class SheetOption:
    """یک گزینه از منوی شیت‌ها: شناسه (همان SheetId)، عنوانِ فارسی، و اینکه صفحه روی آن باز شده."""

    sheet_id: str
    title: str
    selected: bool = False


@dataclass
class SheetContent:
    """محتوای یک شیت.

    kind:
      "datasource"  داده‌ی ساختاریافته‌ی کدال (cells، alias و code پر است)
      "html"        صفحه datasource نداشت؛ جدول‌ها و متنِ خودِ HTML در tables و text است
      "empty"       نه datasource بود، نه جدول یا متنِ معنی‌دار
    """

    sheet_id: str | None
    kind: str
    code: Any = None
    alias: str | None = None
    title: str | None = None
    cells: list[dict] = field(default_factory=list, repr=False)
    tables: list[pd.DataFrame] = field(default_factory=list, repr=False)
    text: str = field(default="", repr=False)
    url: str | None = None
    raw: Any = field(default=None, repr=False)

    @property
    def has_data(self) -> bool:
        return self.kind != "empty"

    def labels(self, limit: int | None = None) -> list[str]:
        """برچسبِ ردیف‌ها به ترتیبِ صفحه (ستونِ اول، بدونِ سرستون)."""
        out: list[str] = []
        if self.kind == "datasource":
            body = [c for c in self.cells
                    if get_ci(c, "columnSequence") == 1 and get_ci(c, "cellGroupName") != "Header"]
            body.sort(key=lambda c: (int(get_ci(c, "rowSequence") or 0)))
            for cell in body:
                value = get_ci(cell, "value")
                if value is not None and str(value).strip():
                    out.append(str(value).strip())
        elif self.tables:
            for table in self.tables:
                if table.shape[1] == 0:
                    continue
                for value in table.iloc[:, 0].tolist():
                    if value is not None and str(value).strip() and str(value).strip().lower() != "nan":
                        out.append(str(value).strip())
        elif self.text:
            out = [line for line in self.text.splitlines() if line.strip()]
        return out if limit is None else out[:limit]

    def to_dict(self, *, label_limit: int = 8) -> dict[str, Any]:
        return {
            "sheet_id": self.sheet_id,
            "kind": self.kind,
            "code": self.code,
            "alias": self.alias,
            "title": self.title,
            "cells": len(self.cells),
            "tables": [list(t.shape) for t in self.tables],
            "text_chars": len(self.text),
            "labels": self.labels(label_limit),
            "url": self.url,
        }


def _clean_text(fragment: str) -> str:
    text = _html.unescape(_TAG_RE.sub(" ", fragment))
    return _WS_RE.sub(" ", text).strip()


def _attr_value(match: re.Match[str]) -> str:
    return next((g for g in match.groups()[1:] if g is not None), "")


def parse_sheet_menu(html: str) -> list[SheetOption]:
    """گزینه‌های منوی شیت‌ها را از HTMLِ صفحه‌ی گزارش درمی‌آورد (خالی اگر منو نبود).

    گزینه‌ی تکراری (همان شناسه) فقط یک بار می‌آید؛ گزینه‌ی بدونِ value نادیده گرفته می‌شود.
    """
    if not html:
        return []
    select = _SELECT_RE.search(html)
    if select is None:
        return []
    options: list[SheetOption] = []
    seen: set[str] = set()
    for match in _OPTION_RE.finditer(select.group("body")):
        attrs = match.group("attrs") or ""
        value_match = _VALUE_RE.search(attrs)
        if value_match is None:
            continue
        sheet_id = _html.unescape(next(g for g in value_match.groups() if g is not None)).strip()
        if not sheet_id or sheet_id in seen:
            continue
        seen.add(sheet_id)
        options.append(SheetOption(
            sheet_id=sheet_id,
            title=_clean_text(match.group("text") or ""),
            selected=bool(_SELECTED_RE.search(attrs)),
        ))
    return options


def sheet_menu_attrs(html: str) -> dict[str, str]:
    """id و name منو (برای عیب‌یابی)؛ خالی اگر منو نبود."""
    select = _SELECT_RE.search(html or "")
    if select is None:
        return {}
    return {m.group(1).lower(): _attr_value(m) for m in _ATTR_RE.finditer(select.group("attrs"))}


def list_sheets(letter: Letter, *, client: Any | None = None, use_cache: bool = True) -> list[SheetOption]:
    """شیت‌هایی که این اطلاعیه دارد، از منوی خودِ صفحه‌ی کدال (یک درخواست)."""
    html, _url = fetch_report_html(letter, client=client, use_cache=use_cache)
    return parse_sheet_menu(html)


def _leaf_tables(soup: BeautifulSoup) -> list[pd.DataFrame]:
    tables: list[pd.DataFrame] = []
    for table in soup.find_all("table"):
        if table.find("table") is not None:
            continue  # فقط جدول‌های برگ؛ وگرنه سلول‌های جدولِ تودرتو دو بار شمرده می‌شوند
        rows: list[list[str | None]] = []
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"], recursive=False)
            row = [_WS_RE.sub(" ", c.get_text(" ", strip=True)).strip() or None for c in cells]
            if any(row):
                rows.append(row)
        if not rows:
            continue
        width = max(len(r) for r in rows)
        if width < 2 and len(rows) < 2:
            continue  # یک خانه‌ی تنها جدولِ داده نیست (معمولاً قاب یا دکمه است)
        padded = [r + [None] * (width - len(r)) for r in rows]
        df = pd.DataFrame(padded)
        tables.append(df.loc[:, df.notna().any(axis=0)])
    return tables


def extract_html_content(html: str) -> tuple[list[pd.DataFrame], str]:
    """جدول‌های برگ و متنِ خوانای صفحه (بدون منو، اسکریپت و کنترل‌های فرم)."""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup.find_all(_NON_CONTENT_TAGS):
        tag.decompose()
    tables = _leaf_tables(soup)
    root = soup.body or soup
    lines: list[str] = []
    for raw_line in root.get_text("\n").splitlines():
        line = _WS_RE.sub(" ", raw_line).strip()
        if len(line) < 2:
            continue
        if lines and lines[-1] == line:
            continue
        lines.append(line)
    return tables, "\n".join(lines)


def auditor_text_from_html(html: str) -> str:
    """متنِ گزارشِ حسابرس از صفحه‌ی HTMLِ شیت 19؛ رشته‌ی خالی اگر متنی شبیهِ گزارش حسابرس نبود.

    متنِ صفحه از اولین نشانه‌ی گزارشِ حسابرس («گزارش حسابرس مستقل»، «به مجمع عمومی»، ...) به بعد
    برگردانده می‌شود تا سربرگ و اطلاعاتِ عمومیِ صفحه داخلش نیاید.
    """
    _tables, text = extract_html_content(html)
    if not text:
        return ""
    lines = text.splitlines()
    norm_lines = [normalize_fa(line) for line in lines]
    for marker in _AUDITOR_START_MARKERS:
        for index, norm in enumerate(norm_lines):
            if marker in norm:
                return "\n".join(lines[index:]).strip()
    return ""


def _sheet_meta(ds: Any) -> tuple[Any, str | None, str | None]:
    for sheet in get_ci(ds, "sheets") or []:
        if isinstance(sheet, dict):
            return get_ci(sheet, "code"), get_ci(sheet, "aliasName"), get_ci(sheet, "title_Fa")
    return None, None, None


def _all_cells(ds: Any) -> list[dict]:
    cells: list[dict] = []
    for sheet in get_ci(ds, "sheets") or []:
        for table in get_ci(sheet, "tables") or []:
            cells.extend(get_ci(table, "cells") or [])
    return cells


def read_sheet(
    letter: Letter,
    sheet_id: str | None,
    *,
    client: Any | None = None,
    use_cache: bool = True,
) -> SheetContent:
    """یک شیت را می‌خواند؛ اول datasource، و اگر نبود جدول‌ها و متنِ HTMLِ همان صفحه.

    فقط خطای شبکه (NetworkError) بالا می‌رود؛ نبودنِ datasource خطا نیست و kind="html" یا "empty" می‌شود.
    """
    html, url = fetch_report_html(letter, sheet_id=sheet_id, client=client, use_cache=use_cache)
    ds = extract_datasource(html)
    if isinstance(ds, dict):
        code, alias, title = _sheet_meta(ds)
        # aliasName کدال را عیناً حفظ می‌کنیم؛ ممکن است با محتوای شیت ناهماهنگ باشد.
        # عنوانِ منوی همان گزارش برای نمایش به کاربر مرجع مناسب‌تری است.
        menu_title = next((o.title for o in parse_sheet_menu(html) if o.sheet_id == str(sheet_id)), None)
        title = menu_title or title
        return SheetContent(sheet_id=sheet_id, kind="datasource", code=code, alias=alias, title=title,
                            cells=_all_cells(ds), url=url, raw=ds)
    tables, text = extract_html_content(html)
    selected = next((o.title for o in parse_sheet_menu(html) if o.sheet_id == str(sheet_id)), None)
    kind = "html" if (tables or text) else "empty"
    return SheetContent(sheet_id=sheet_id, kind=kind, title=selected, tables=tables, text=text, url=url)


def same_alias(a: str | None, b: str | None) -> bool:
    """دو aliasName با نادیده گرفتنِ خط تیره و بزرگی/کوچکیِ حروف یکی‌اند؟"""
    return bool(a) and bool(b) and normalize_alias(a) == normalize_alias(b)


def require_datasource(content: SheetContent, letter: Letter) -> dict:
    """اگر شیت datasource نداشت DataSourceError؛ برای کدی که فقط داده‌ی ساختاریافته می‌خواهد."""
    if content.kind != "datasource":
        raise DataSourceError(
            f"شیت {content.sheet_id} در اطلاعیه‌ی TracingNo={letter.tracing_no} داده‌ی ساختاریافته "
            f"(datasource) ندارد؛ محتوای آن {'HTML' if content.kind == 'html' else 'خالی'} است.",
            tracing_no=letter.tracing_no,
            url=content.url,
        )
    return content.raw
