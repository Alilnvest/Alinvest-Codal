"""بررسی‌های کیفیت و هم‌خوانیِ صورت‌های مالیِ کدال.

این ماژول خالص است: بدونِ شبکه، بدونِ چاپ، و بدونِ وابستگی به نماد. ورودی FinancialReport یا
PortfolioReport است و خروجی فهرستی از CheckResult. وضعیت‌ها:

  ok    بررسی انجام شد و درست بود
  warn  ناهم‌خوانیِ واقعی؛ باید دیده شود
  info  بررسی ممکن نبود (برچسب یا قالبِ ناشناخته) یا توضیحِ عادی
  error داده‌ی خراب یا ناقص که اصلاً نمی‌شود خواندش
  skip  پیش‌نیاز نبود، پس اجرا نشد

همه‌ی مقایسه‌ها با تاریخِ پایانِ دوره انجام می‌شوند، نه با حرفِ ستون؛ چون ستون‌ها بین
ترازنامه و سود و زیان فرق دارند (ترازنامه C = پایان سال قبل است، نه سه‌ماههٔ سال قبل).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterable

from .formulas import verify_formulas
from .utils import normalize_fa

__all__ = [
    "CheckResult", "STATUSES", "TOLERANCE", "row_key", "to_number", "index_rows",
    "column_layout", "data_columns", "column_title", "period_of", "statement_kind",
    "cash_flow_method", "is_direct_method", "net_profit_row", "find_exact", "find_tokens",
    "analyze_statement", "analyze_set", "analyze_portfolio", "summarize",
]

TOLERANCE = 1.0  # ریال؛ مقدارها صحیح‌اند
STATUSES = ("ok", "warn", "error", "info", "skip")
COLUMN_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_EN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_NUMBER = re.compile(r"^[+-]?\d+(\.\d+)?$")
_YEAR_END_DAYS = ("12/29", "12/30", "12/31")
_PERIOD_ENDS = {3: ("03/31",), 6: ("06/31",), 9: ("09/30",), 12: _YEAR_END_DAYS}
_ALIAS_KIND = {
    "IncomeStatement": "income", "ConsolidatedIncomeStatement": "income",
    "BalanceSheet": "balance", "ConsolidatedBalanceSheet": "balance",
    "CashFlow": "cash_flow", "ConsolidatedCashFlow": "cash_flow",
    "ChangesInPropertyRights": "equity", "ConsolidatedChangesInPropertyRights": "equity",
    "ComprehensiveIncomeStatement": "comprehensive", "ConsolidatedComprehensiveIncomeStatement": "comprehensive",
}
_ALIAS_KIND_NORM = {re.sub(r"[^0-9a-z]+", "", k.lower()): v for k, v in _ALIAS_KIND.items()}
_TITLE_KIND = (("جامع", "comprehensive"), ("حقوقمالکانه", "equity"), ("جریان", "cash_flow"),
               ("وجوهنقد", "cash_flow"), ("ترازنامه", "balance"), ("وضعیتمالی", "balance"),
               ("سودوزیان", "income"))
KIND_TITLE = {"income": "سود و زیان", "balance": "ترازنامه", "cash_flow": "جریان نقد",
              "equity": "تغییرات حقوق مالکانه", "comprehensive": "سود و زیان جامع",
              "unknown": "صورت ناشناخته"}


@dataclass(frozen=True)
class CheckResult:
    status: str
    section: str
    name: str
    message: str
    details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"وضعیت نامعتبر: {self.status!r}")

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "section": self.section, "name": self.name,
                "message": self.message, "details": list(self.details)}


def _r(status: str, section: str, name: str, message: str, details: Iterable[str] = ()) -> CheckResult:
    return CheckResult(status, section, name, message, tuple(details))


def fa_digits(text: Any) -> str:
    return str(text).translate(_FA_DIGITS)


_DISPLAY_MAP = str.maketrans({"ي": "ی", "ك": "ک"})


def display_fa(text: Any) -> str:
    """حروفِ عربیِ متنِ کدال را برای نمایش به فارسی تبدیل می‌کند؛ مقایسه‌ها همچنان با row_key انجام می‌شوند."""
    return str(text).translate(_DISPLAY_MAP)


def summarize(results: Iterable[CheckResult]) -> dict[str, int]:
    counts = {status: 0 for status in STATUSES}
    for result in results:
        counts[result.status] += 1
    return counts


# ---------------------------------------------------------------- متن و عدد
def row_key(text: Any) -> str:
    """کلیدِ مقایسه‌ی برچسب: حروفِ یکدست، بدونِ فاصله، نیم‌فاصله و علامت‌گذاری."""
    return "".join(ch for ch in normalize_fa(str(text or "")) if ch.isalnum())


def to_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = str(value).strip().replace(",", "").replace("٬", "").translate(_EN_DIGITS)
    if not text or text.lower() in ("nan", "none"):
        return None
    if not _NUMBER.match(text):
        return None
    number = float(text)
    return number if math.isfinite(number) else None


def _is_label(value: Any) -> bool:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return False
    text = str(value).strip()
    return bool(text) and text != "--" and to_number(text) is None


def _close(a: float, b: float, tolerance: float = TOLERANCE) -> bool:
    return abs(a - b) <= tolerance


def index_rows(df: Any) -> list[dict[str, Any]]:
    """هر ردیفِ جدول: برچسب، کلید، و عددها به‌تفکیکِ ستون (vals[ستون])."""
    rows: list[dict[str, Any]] = []
    if df is None or len(df) == 0:
        return rows
    for code, series in df.iterrows():
        label, vals, nums = "", {}, []
        for col in df.columns:
            value = series[col]
            if not label and _is_label(value):
                label = str(value).strip()
                continue
            number = to_number(value)
            if number is not None:
                vals[int(col)] = number
                nums.append(number)
        rows.append({"code": code, "label": label, "key": row_key(label), "vals": vals, "nums": nums})
    return rows


def find_exact(rows: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    return next((r for r in rows if r["key"] == key), None)


def find_tokens(rows: list[dict[str, Any]], *tokens: str, exclude: Iterable[str] = ()) -> dict[str, Any] | None:
    """ردیفی که همه‌ی کلیدواژه‌ها را دارد و هیچ‌کدام از حذفی‌ها را ندارد؛ مشخص‌ترین (کوتاه‌ترین کلید) برنده است."""
    excluded = tuple(exclude)
    hits = [r for r in rows if all(t in r["key"] for t in tokens) and not any(x in r["key"] for x in excluded)]
    if not hits:
        return None
    return min(hits, key=lambda r: (len(r["key"]), str(r["code"])))


def _candidates(rows: list[dict[str, Any]], *tokens: str, limit: int = 8) -> list[str]:
    labels = [r["label"] for r in rows if r["label"] and any(t in r["key"] for t in tokens)]
    return labels[:limit]


# ---------------------------------------------------------------- ستون‌ها و دوره‌ها
def _audit_state(header: str) -> bool | None:
    key = row_key(header)
    if "حسابرسی" not in key:
        return None
    return "نشده" not in key


def column_layout(raw: dict | None) -> dict[int, dict[str, Any]]:
    """ساختار ستون‌ها از سرستونِ خودِ کدال: سرستون (فارسی‌شده)، تاریخ دوره، درصدی بودن، پنهان بودن، و وضعیت حسابرسی."""
    cells = [c for sheet in ((raw or {}).get("sheets") or [])
             for t in (sheet.get("tables") or []) for c in (t.get("cells") or [])]
    acc: dict[int, dict[str, Any]] = {}
    for c in cells:
        seq = c.get("columnSequence")
        if seq is None:
            continue
        entry = acc.setdefault(int(seq), {"texts": [], "period": "", "total": 0, "hidden": 0})
        entry["total"] += 1
        if c.get("isVisible") is False:
            entry["hidden"] += 1
        text = str(c.get("value") or "").strip()
        if c.get("cellGroupName") == "Header" and text:
            entry["texts"].append((int(c.get("rowCode") or 0), text))
        if not entry["period"] and c.get("periodEndToDate"):
            entry["period"] = str(c["periodEndToDate"])
    layout: dict[int, dict[str, Any]] = {}
    for seq, entry in acc.items():
        raw_header = " / ".join(text for _, text in sorted(entry["texts"]))
        layout[seq] = {
            "header": display_fa(raw_header),
            "period": entry["period"],
            "percent": "درصد" in row_key(raw_header),
            "hidden": entry["total"] > 0 and entry["hidden"] == entry["total"],
            "audited": _audit_state(raw_header),
        }
    return layout


def data_columns(layout: dict[int, dict[str, Any]]) -> list[int]:
    """ستون‌های داده: بعد از برچسب، غیردرصدی و قابل‌نمایش."""
    return [seq for seq, e in sorted(layout.items()) if seq > 1 and not e["percent"] and not e["hidden"]]


def column_title(layout: dict[int, dict[str, Any]], seq: int) -> str:
    name = COLUMN_LETTERS[seq - 1] if 1 <= seq <= len(COLUMN_LETTERS) else str(seq)
    header = (layout.get(seq) or {}).get("header") or ""
    return f"ستون {name}" + (f" ({fa_digits(header)})" if header else "")


def period_of(layout: dict[int, dict[str, Any]], seq: int) -> str:
    return (layout.get(seq) or {}).get("period", "") or ""


def _period_map(report: Any) -> dict[str, int]:
    layout = column_layout(report.raw)
    return {period_of(layout, s): s for s in data_columns(layout) if period_of(layout, s)}


def _months(report: Any) -> int | None:
    raw = report.raw if isinstance(report.raw, dict) else {}
    try:
        return int(raw.get("period")) if raw.get("period") is not None else None
    except (TypeError, ValueError):
        return None


def statement_kind(report: Any) -> str:
    raw = report.raw if isinstance(report.raw, dict) else {}
    sheets = raw.get("sheets") or [{}]
    alias = str((sheets[0] or {}).get("aliasName") or "")
    if alias in _ALIAS_KIND:
        return _ALIAS_KIND[alias]
    alias_norm = re.sub(r"[^0-9a-z]+", "", alias.lower())
    if alias_norm in _ALIAS_KIND_NORM:
        return _ALIAS_KIND_NORM[alias_norm]
    title = row_key(getattr(report, "title", "") or "")
    for token, kind in _TITLE_KIND:
        if token in title:
            return kind
    return "unknown"


# ---------------------------------------------------------------- روش جریان نقد و سود خالص
def net_profit_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    exact = find_exact(rows, row_key("سود (زیان) خالص"))
    if exact is not None:
        return exact
    return find_tokens(rows, "سود", "خالص",
                       exclude=("سهم", "عملیاتی", "قبل", "پیش", "جامع", "پس", "مالیات", "ماهانه", "متوقف"))


def cash_flow_method(rows: list[dict[str, Any]]) -> str:
    """غیرمستقیم: ردیف سود خالص در بخش عملیاتی دارد. مستقیم: دریافت از مشتری یا پرداخت به تأمین‌کننده دارد."""
    if net_profit_row(rows) is not None:
        return "indirect"
    if any(("دریافتهاینقدی" in r["key"] and ("فروش" in r["key"] or "مشتری" in r["key"]))
           or ("پرداختهاینقدی" in r["key"] and ("کالا" in r["key"] or "تامین" in r["key"] or "تأمین" in r["key"]))
           for r in rows):
        return "direct"
    return "unknown"


def is_direct_method(rows: list[dict[str, Any]]) -> bool:
    return cash_flow_method(rows) == "direct"


# ---------------------------------------------------------------- بررسی‌های تک‌صورت
def check_formulas(raw: dict | None, section: str) -> list[CheckResult]:
    rep = verify_formulas(raw or {})
    if rep.checked == 0 and not rep.unsupported:
        return [_r("info", section, "فرمول‌های کدال", "این صورت فرمول محاسباتی ندارد")]
    out = []
    if rep.mismatches:
        out.append(_r("warn", section, "فرمول‌های کدال",
                      f"{len(rep.mismatches)} خانه با فرمول کدال نمی‌خواند (از {rep.checked})",
                      [f"{m['address']}: {m['formula'][:70]} → محاسبه {m['computed']} / ذخیره {m['stored']}"
                       for m in rep.mismatches[:8]]))
    else:
        out.append(_r("ok", section, "فرمول‌های کدال",
                      f"{rep.matched} جمع یا عدد درست؛ {rep.undefined} درصد نامعین (--)"))
    if rep.unsupported:
        out.append(_r("info", section, "فرمول‌های ناپشتیبانی‌شده",
                      f"{len(rep.unsupported)} فرمول خارج از پشتیبانی بود و بررسی نشد",
                      [f"{u['address']}: {u['formula'][:70]}" for u in rep.unsupported[:8]]))
    return out


def check_structure(report: Any, layout: dict[int, dict[str, Any]], section: str,
                    kind: str | None = None) -> list[CheckResult]:
    out: list[CheckResult] = []
    data = data_columns(layout)
    if not data:
        return [_r("error", section, "ستون‌های داده", "هیچ ستون عددی قابل‌نمایشی پیدا نشد")]
    if kind == "equity":
        # در صورت تغییرات حقوق مالکانه ستون‌ها اجزای سرمایه‌اند، نه دوره؛ پس با نامِ خودشان می‌آیند
        out.append(_r("ok", section, "اجزای حقوق مالکانه", "؛ ".join(column_title(layout, s) for s in data)))
    else:
        out.append(_r("ok", section, "ستون‌های داده", "؛ ".join(column_title(layout, s) for s in data)))
        if not any(e["percent"] for e in layout.values()):
            out.append(_r("info", section, "ستون درصد تغییر", "ستون درصد تغییر پیدا نشد (ممکن است قالب متفاوت باشد)"))
    months = _months(report)
    end = str(report.period_end_to_date or "")
    if not end:
        out.append(_r("error", section, "دوره و تاریخ پایان", "تاریخ پایان دوره در داده نیست"))
    elif months in _PERIOD_ENDS:
        if end[5:] in _PERIOD_ENDS[months]:
            out.append(_r("ok", section, "دوره و تاریخ پایان", f"دورهٔ {months} ماهه با پایان {fa_digits(end)} سازگار است"))
        else:
            out.append(_r("warn", section, "دوره و تاریخ پایان",
                          f"دورهٔ {months} ماهه ولی تاریخ پایان {fa_digits(end)} با آن نمی‌خواند"))
    else:
        out.append(_r("info", section, "دوره و تاریخ پایان",
                      f"تاریخ پایان {fa_digits(end)}؛ دورهٔ {months} ماهه برای بررسی شناخته نشد"))
    return out


def check_audit(report: Any, layout: dict[int, dict[str, Any]], section: str) -> list[CheckResult]:
    out = []
    data = data_columns(layout)
    top = getattr(report, "is_audited", None)
    if data:
        first = layout[data[0]]["audited"]
        if first is not None and top is not None and first != top:
            out.append(_r("warn", section, "وضعیت حسابرسی",
                          f"وضعیت حسابرسی ستون اول با وضعیت کلیِ اطلاعیه یکی نیست (ستون: {'حسابرسی‌شده' if first else 'حسابرسی‌نشده'})"))
    if _months(report) == 12:
        if data and layout[data[0]]["audited"] is False:
            out.append(_r("warn", section, "وضعیت حسابرسی", "صورت سالانه باید حسابرسی‌شده باشد، ولی ستون اول حسابرسی‌نشده است"))
        return out
    year_end_cols = [s for s in data if period_of(layout, s)[5:] in _YEAR_END_DAYS]
    if not year_end_cols:
        out.append(_r("info", section, "حسابرسیِ سال مالی قبل", "ستون پایان سال مالی قبل پیدا نشد؛ این بررسی انجام نشد"))
    elif layout[year_end_cols[0]]["audited"]:
        out.append(_r("ok", section, "حسابرسیِ سال مالی قبل", f"{column_title(layout, year_end_cols[0])} حسابرسی‌شده است"))
    else:
        out.append(_r("warn", section, "حسابرسیِ سال مالی قبل",
                      "ستون پایان سال مالی قبل حسابرسی‌شده نیست؛ برای میان‌دوره انتظار می‌رود باشد"))
    return out


def check_balance(rows: list[dict[str, Any]], layout: dict[int, dict[str, Any]], section: str) -> list[CheckResult]:
    name = "جمع دارایی‌ها = بدهی‌ها + حقوق مالکانه"
    assets = find_exact(rows, row_key("جمع دارایی‌ها"))
    both = (find_exact(rows, row_key("جمع بدهی‌ها و حقوق مالکانه"))
            or find_exact(rows, row_key("جمع حقوق مالکانه و بدهی‌ها")))
    liabilities = find_exact(rows, row_key("جمع بدهی‌ها"))
    equity = find_exact(rows, row_key("جمع حقوق مالکانه"))
    if assets is None or (both is None and (liabilities is None or equity is None)):
        return [_r("info", section, name, "برچسب‌های جمع دارایی‌ها یا جمع بدهی‌ها و حقوق مالکانه پیدا نشد؛ این بررسی انجام نشد",
                   _candidates(rows, "دارایی", "بدهی", "حقوقمالکانه"))]
    out = []
    for col in data_columns(layout):
        a = assets["vals"].get(col)
        if a is None:
            continue
        if both is not None and both["vals"].get(col) is not None:
            right, how = both["vals"][col], "جمع بدهی‌ها و حقوق مالکانه"
        elif liabilities and equity and liabilities["vals"].get(col) is not None and equity["vals"].get(col) is not None:
            right, how = liabilities["vals"][col] + equity["vals"][col], "بدهی‌ها + حقوق مالکانه"
        else:
            continue
        if _close(a, right):
            out.append(_r("ok", section, name, f"{column_title(layout, col)}: {a:,.0f} = {how}"))
        else:
            out.append(_r("warn", section, name, f"{column_title(layout, col)}: {a:,.0f} ≠ {how} ({right:,.0f})"))
    return out or [_r("info", section, name, "ستون عددی برای مقایسه پیدا نشد")]


def check_income(rows: list[dict[str, Any]], layout: dict[int, dict[str, Any]], section: str) -> list[CheckResult]:
    name = "سود ناخالص = درآمد و بهای تمام‌شده"
    revenue = (find_exact(rows, row_key("درآمدهای عملیاتی"))
               or find_tokens(rows, row_key("درآمدهای عملیاتی"), exclude=("بها",)))
    cost = (find_exact(rows, row_key("بهای تمام شده درآمدهای عملیاتی"))
            or find_tokens(rows, "بهایتمامشده", row_key("درآمدهای عملیاتی")))
    gross = (find_exact(rows, row_key("سود (زیان) ناخالص"))
             or find_tokens(rows, "سود", "ناخالص"))
    if not (revenue and cost and gross):
        return [_r("info", section, name, "ردیف‌های درآمد، بهای تمام‌شده یا سود ناخالص پیدا نشد؛ این بررسی انجام نشد",
                   _candidates(rows, "درامد", "بها", "سود"))]
    out = []
    for col in data_columns(layout):
        r_, c_, g_ = revenue["vals"].get(col), cost["vals"].get(col), gross["vals"].get(col)
        if None in (r_, c_, g_):
            continue
        if _close(g_, r_ + c_) or _close(g_, r_ - c_):
            out.append(_r("ok", section, name, f"{column_title(layout, col)}: {g_:,.0f}"))
        else:
            out.append(_r("warn", section, name, f"{column_title(layout, col)}: سود ناخالص {g_:,.0f} ≠ درآمد {r_:,.0f} و بهای تمام‌شده {c_:,.0f}"))
    return out or [_r("info", section, name, "ستون عددی برای مقایسه پیدا نشد")]


def check_cash_flow(rows: list[dict[str, Any]], layout: dict[int, dict[str, Any]], section: str) -> list[CheckResult]:
    out: list[CheckResult] = []
    method = cash_flow_method(rows)
    out.append(_r("info", section, "روش تهیه‌ی جریان نقد",
                  {"direct": "روش مستقیم؛ سود خالص در این صورت نیست",
                   "indirect": "روش غیرمستقیم؛ از سود خالص شروع می‌شود",
                   "unknown": "روش تشخیص داده نشد"}[method]))
    operating = find_tokens(rows, "خالص", "عملیاتی")
    investing = find_tokens(rows, "خالص", "سرمایهگذاری")
    before = find_tokens(rows, "خالص", "قبل")
    financing = find_tokens(rows, "خالص", "مالی", exclude=("قبل", "مالیات"))
    change = (find_tokens(rows, "موجودینقد", "افزایش", exclude=("ابتدا", "پایان"))
              or find_tokens(rows, "موجودینقد", "کاهش", exclude=("ابتدا", "پایان")))
    begin = find_tokens(rows, "موجودینقد", "ابتدا")
    end = find_tokens(rows, "موجودینقد", "پایان")
    fx = find_tokens(rows, "نرخارز")
    cash_ops = find_exact(rows, row_key("نقد حاصل از عملیات")) or find_tokens(rows, "نقدحاصلازعملیات")
    tax_paid = find_tokens(rows, "مالیات", "پرداخت")
    missing = [label for label, row in (("عملیاتی", operating), ("سرمایه‌گذاری", investing),
                                        ("تأمین مالی", financing)) if row is None]
    if missing:
        out.append(_r("info", section, "بخش‌های جریان نقد",
                      "بخش‌های «" + "»، «".join(missing) + "» پیدا نشد؛ هم‌خوانیِ مربوط انجام نشد",
                      _candidates(rows, "خالص", limit=8)))

    def at(row: dict[str, Any] | None, col: int) -> float | None:
        return None if row is None else row["vals"].get(col)

    for col in data_columns(layout):
        name_col = column_title(layout, col)
        o, i_, b, f = at(operating, col), at(investing, col), at(before, col), at(financing, col)
        ch, bg, en, fxv = at(change, col), at(begin, col), at(end, col), at(fx, col)
        if None not in (o, i_, b):
            ok = _close(b, o + i_)
            out.append(_r("ok" if ok else "warn", section, "قبل از تأمین مالی = عملیاتی + سرمایه‌گذاری",
                          f"{name_col}: {b:,.0f}" if ok else f"{name_col}: {b:,.0f} ≠ {o:,.0f} + {i_:,.0f}"))
        if None not in (b, f, ch):
            ok = _close(ch, b + f)
            out.append(_r("ok" if ok else "warn", section, "تغییر خالص = قبل از تأمین مالی + تأمین مالی",
                          f"{name_col}: {ch:,.0f}" if ok else f"{name_col}: {ch:,.0f} ≠ {b:,.0f} + {f:,.0f}"))
        if None not in (bg, ch, en):
            if fxv is None:
                continue
            expected = bg + ch + fxv
            ok = _close(en, expected)
            out.append(_r("ok" if ok else "warn", section, "پایان دوره = ابتدا + تغییر (+ اثر ارز)",
                          f"{name_col}: {en:,.0f}" if ok else f"{name_col}: {en:,.0f} ≠ {expected:,.0f}"))
        co, tp, oo = at(cash_ops, col), at(tax_paid, col), o
        if None not in (co, tp, oo):
            ok = _close(oo, co + tp)
            out.append(_r("ok" if ok else "warn", section, "عملیاتی = نقد حاصل از عملیات + مالیات پرداختی",
                          f"{name_col}: {oo:,.0f}" if ok else f"{name_col}: {oo:,.0f} ≠ {co:,.0f} + {tp:,.0f}"))
    cols = data_columns(layout)
    if begin is not None and cols and "سال" in begin["key"]:
        current_open = at(begin, cols[0])
        for col in cols[1:]:
            if period_of(layout, col)[5:] in _YEAR_END_DAYS:
                prior_close = at(end, col)
                if current_open is not None and prior_close is not None:
                    ok = _close(current_open, prior_close)
                    text = f"ابتدای سال جاری {current_open:,.0f}، پایان سال مالی {fa_digits(period_of(layout, col))} {prior_close:,.0f}"
                    out.append(_r("ok" if ok else "warn", section, "ابتدای سال جاری = پایان سال مالی قبل", text))
                break
    return out


def check_scope(report: Any, raw: dict[str, Any], section: str) -> list[CheckResult]:
    """نوعِ اطلاعیه (تلفیقی/غیرتلفیقی) باید با خودِ شیت بخواند.

    کدال به شیتِ تلفیقی aliasName با پیشوند Consolidated می‌دهد. اگر اطلاعیه تلفیقی است ولی شیتِ برگشتی بی‌پیشوند
    است، ارقام مالِ شرکتِ مادرند و با برچسبِ تلفیقی گمراه‌کننده‌اند (باگِ شناسه‌ی ۱۰۶۰/۱۰۵۸ در نسخه‌های قبلی)؛ برعکسش هم.
    """
    aliases = []
    for sheet in raw.get("sheets") or []:
        if isinstance(sheet, dict):
            candidate = str(sheet.get("aliasName") or "")
            candidate_norm = re.sub(r"[^0-9a-z]+", "", candidate.lower())
            if candidate_norm in _ALIAS_KIND_NORM:
                aliases.append((candidate, candidate_norm))
    if not aliases:
        return []
    claimed = bool(getattr(report, "is_consolidated", False))
    matching = [item for item in aliases if item[1].startswith("consolidated") == claimed]
    if matching:
        return []
    alias, alias_norm = aliases[0]
    sheet_is_consolidated = alias_norm.startswith("consolidated")
    claimed_fa = "تلفیقی" if claimed else "غیرتلفیقی"
    sheet_fa = "تلفیقی" if sheet_is_consolidated else "غیرتلفیقی"
    return [CheckResult("warn", section, "هم‌خوانی نوع اطلاعیه و شیت",
                        f"اطلاعیه {claimed_fa} است ولی شیت دریافتی ({alias}) {sheet_fa} است؛ ارقام ممکن است مالِ "
                        "شرکتِ مادر باشند نه گروه.")]


def analyze_statement(report: Any, *, kind: str | None = None) -> list[CheckResult]:
    """همه‌ی بررسی‌های یک صورت: فرمول‌ها، ساختار، دوره، حسابرسی، و هم‌خوانی‌های همان صورت."""
    kind = kind or statement_kind(report)
    section = f"صورت: {KIND_TITLE.get(kind, kind)}"
    raw = report.raw if isinstance(report.raw, dict) else {}
    layout = column_layout(raw)
    out = check_formulas(raw, section)
    out.extend(check_scope(report, raw, section))
    out.extend(check_structure(report, layout, section, kind))
    out.extend(check_audit(report, layout, section))
    rows = index_rows(report.row_code_df)
    if kind == "balance":
        out.extend(check_balance(rows, layout, section))
    elif kind == "income":
        out.extend(check_income(rows, layout, section))
    elif kind == "cash_flow":
        out.extend(check_cash_flow(rows, layout, section))
    return out


# ---------------------------------------------------------------- هم‌خوانی بین صورت‌ها
_LINK = "هم‌خوانی بین صورت‌ها"


def _check_cash_links(bal: Any, cash: Any) -> list[CheckResult]:
    name = "موجودی نقد: ترازنامه ↔ جریان نقد"
    bal_rows, cash_rows = index_rows(bal.row_code_df), index_rows(cash.row_code_df)
    cash_row = (find_exact(bal_rows, row_key("موجودی نقد"))
                or find_tokens(bal_rows, "موجودینقد", exclude=("ابتدا", "پایان")))
    end_row = find_tokens(cash_rows, "موجودینقد", "پایان")
    begin_row = find_tokens(cash_rows, "موجودینقد", "ابتدا")
    if cash_row is None or end_row is None or begin_row is None:
        return [_r("info", _LINK, name, "ردیف موجودی نقد در ترازنامه یا ابتدا/پایان در جریان نقد پیدا نشد",
                   _candidates(bal_rows, "نقد", limit=6))]
    cash_layout = column_layout(cash.raw)
    bal_periods = _period_map(bal)
    out = []
    for col in data_columns(cash_layout):
        date = period_of(cash_layout, col)
        for label, target, cf_value in (("پایان دوره", date, end_row["vals"].get(col)),
                                        ("ابتدای سال", (date[:5] + "01/01") if date else "", begin_row["vals"].get(col))):
            bcol = bal_periods.get(target)
            if bcol is None or cf_value is None or cash_row["vals"].get(bcol) is None:
                continue
            bal_value = cash_row["vals"][bcol]
            text = f"{label} {fa_digits(target)}: ترازنامه {bal_value:,.0f} ↔ جریان نقد {cf_value:,.0f}"
            if _close(bal_value, cf_value):
                out.append(_r("ok", _LINK, name, text))
            else:
                out.append(_r("warn", _LINK, name, text + " -- ناهم‌خوان",
                              [f"ستون جریان نقد: {column_title(cash_layout, col)}",
                               "احتمال: ارقام تجدیدارائه‌شده یک‌دست نیستند یا تعریف موجودی نقد فرق دارد"]))
    if not out:
        out.append(_r("info", _LINK, name, "هیچ تاریخ مشترکی بین ترازنامه و جریان نقد پیدا نشد"))
    return out


def _q1_window(income: Any, bal: Any) -> tuple[int, int] | None:
    """(ستون B ترازنامه، ستون C ترازنامه) وقتی دورهٔ سه‌ماههٔ بعد از پایان سال مالی قبل است؛ وگرنه None."""
    if _months(income) != 3:
        return None
    bal_layout = column_layout(bal.raw)
    cols = data_columns(bal_layout)
    if len(cols) < 2:
        return None
    b_col, c_col = cols[0], cols[1]
    if period_of(bal_layout, c_col)[5:] not in _YEAR_END_DAYS:
        return None
    if period_of(bal_layout, b_col) != str(income.period_end_to_date or ""):
        return None
    return b_col, c_col


def _check_profit_cash(income: Any, cash: Any) -> list[CheckResult]:
    name = "سود خالص: سود و زیان ↔ جریان نقد"
    cash_rows = index_rows(cash.row_code_df)
    method = cash_flow_method(cash_rows)
    if method == "direct":
        return [_r("info", _LINK, name, "جریان نقد به روش مستقیم است و سود خالص ندارد؛ این مقایسه ممکن نیست")]
    if method != "indirect":
        return [_r("info", _LINK, name, "روش جریان نقد تشخیص داده نشد؛ مقایسه انجام نشد")]
    if _months(cash) != 3 or _months(income) != 3:
        return [_r("info", _LINK, name, "این مقایسه فقط برای دورهٔ سه‌ماهه انجام می‌شود؛ در دوره‌های تجمعی باید دورهٔ هم‌پوشان تطبیق داده شود")]
    income_net = net_profit_row(index_rows(income.row_code_df))
    cash_net = net_profit_row(cash_rows)
    if income_net is None or cash_net is None:
        return [_r("info", _LINK, name, "ردیف سود خالص در یکی از دو صورت پیدا نشد",
                   _candidates(index_rows(income.row_code_df), "سود", limit=6))]
    inc_periods = _period_map(income)
    cash_layout = column_layout(cash.raw)
    out = []
    for col in data_columns(cash_layout):
        date = period_of(cash_layout, col)
        icol = inc_periods.get(date)
        if icol is None or cash_net["vals"].get(col) is None or income_net["vals"].get(icol) is None:
            continue
        a, b = income_net["vals"][icol], cash_net["vals"][col]
        text = f"سود خالص دورهٔ {fa_digits(date)}: سود و زیان {a:,.0f} ↔ جریان نقد {b:,.0f}"
        out.append(_r("ok" if _close(a, b) else "warn", _LINK, name, text))
    return out or [_r("info", _LINK, name, "دورهٔ مشترکی برای مقایسه‌ی سود خالص پیدا نشد")]


def _check_retained_earnings(income: Any, bal: Any, cash: Any) -> list[CheckResult]:
    name = "سود انباشته: ترازنامه ↔ سود خالص سه‌ماهه"
    window = _q1_window(income, bal)
    if window is None:
        return [_r("info", _LINK, name, "این پیوند فقط برای دورهٔ سه‌ماههٔ بعد از پایان سال مالی بررسی می‌شود")]
    b_col, c_col = window
    bal_rows = index_rows(bal.row_code_df)
    re_row = find_exact(bal_rows, row_key("سود (زیان) انباشته")) or find_tokens(bal_rows, "سود", "انباشته")
    net = net_profit_row(index_rows(income.row_code_df))
    if re_row is None or net is None or net["vals"].get(b_col) is None:
        return [_r("info", _LINK, name, "ردیف سود انباشته یا سود خالص پیدا نشد؛ این پیوند انجام نشد")]
    change = re_row["vals"].get(b_col, 0.0) - re_row["vals"].get(c_col, 0.0)
    value = net["vals"][b_col]
    text = f"تغییر سود انباشته {change:,.0f} ↔ سود خالص همان دوره {value:,.0f}"
    if _close(change, value):
        return [_r("ok", _LINK, name, text + " (بدون تقسیم سود در این دوره)")]
    distributed = value - change
    return [_r("info", _LINK, name, text + f" -- تفاوت {distributed:,.0f} ریال: احتمالاً سود تقسیم‌شده یا تعدیل")]


def _check_dividends(bal: Any, cash: Any) -> list[CheckResult]:
    name = "سود سهام پرداختنی: ترازنامه ↔ پرداخت نقدی"
    window = _q1_window(cash, bal)
    if window is None:
        return [_r("info", _LINK, name, "این پیوند فقط برای دورهٔ سه‌ماههٔ بعد از پایان سال مالی بررسی می‌شود")]
    b_col, c_col = window
    bal_rows = index_rows(bal.row_code_df)
    payable = find_exact(bal_rows, row_key("سود سهام پرداختنی"))
    cash_rows = index_rows(cash.row_code_df)
    paid = find_tokens(cash_rows, "پرداختهاینقدی", "سودسهام")
    if payable is None or paid is None:
        return [_r("info", _LINK, name, "ردیف سود سهام پرداختنی یا پرداخت نقدی سود سهام پیدا نشد")]
    cash_layout = column_layout(cash.raw)
    cash_col = next((s for s in data_columns(cash_layout)
                     if period_of(cash_layout, s) == period_of(column_layout(bal.raw), b_col)), None)
    p = paid["vals"].get(cash_col) if cash_col is not None else None
    if p is None or payable["vals"].get(b_col) is None or payable["vals"].get(c_col) is None:
        return [_r("info", _LINK, name, "مقدار لازم برای این پیوند در داده نیست")]
    change = payable["vals"][b_col] - payable["vals"][c_col]
    text = f"تغییر سود سهام پرداختنی {change:,.0f} ↔ پرداخت نقدی سود سهام {p:,.0f}"
    if _close(change, p):
        return [_r("ok", _LINK, name, text)]
    # بدهی = سود مصوبِ همین دوره − پرداخت نقدی؛ و پرداخت نقدی در جریان نقد منفی ثبت می‌شود
    declared = change - p
    if declared > 0:
        return [_r("info", _LINK, name, text + f" -- تفاوت احتمالاً از سود تازه‌مصوبِ همین دوره است؛ مقدار تقریبی {declared:,.0f}")]
    return [_r("info", _LINK, name, text + " -- تفاوت توضیح ساده‌ای ندارد؛ بررسی شود")]


def _check_comprehensive(income: Any, comp: Any) -> list[CheckResult]:
    name = "سود جامع: سود و زیان ↔ سود و زیان جامع"
    if _months(income) != 3 or _months(comp) != 3:
        return [_r("info", _LINK, name, "این مقایسه فقط برای دورهٔ سه‌ماهه انجام می‌شود")]
    inc = net_profit_row(index_rows(income.row_code_df))
    comp_rows = index_rows(comp.row_code_df)
    comp_net = net_profit_row(comp_rows)
    if inc is None or comp_net is None:
        return [_r("info", _LINK, name, "ردیف سود خالص در یکی از دو صورت پیدا نشد")]
    inc_periods = _period_map(income)
    comp_layout = column_layout(comp.raw)
    out = []
    for col in data_columns(comp_layout):
        icol = inc_periods.get(period_of(comp_layout, col))
        if icol is None or comp_net["vals"].get(col) is None or inc["vals"].get(icol) is None:
            continue
        a, b = inc["vals"][icol], comp_net["vals"][col]
        out.append(_r("ok" if _close(a, b) else "warn", _LINK, name,
                      f"سود خالص {fa_digits(period_of(comp_layout, col))}: سود و زیان {a:,.0f} ↔ سود جامع {b:,.0f}"))
    return out or [_r("info", _LINK, name, "دورهٔ مشترکی برای مقایسه پیدا نشد")]


def _check_restatement(cash: Any, prior: Any) -> list[CheckResult]:
    name = "تجدیدارائه: جریان نقد سال قبل در گزارش جاری ↔ گزارش اصلی آن دوره"
    if prior is None:
        return [_r("skip", _LINK, name, "گزارش اصلیِ دورهٔ سال قبل داده نشد")]
    cur_col = _period_map(cash).get(prior.period_end_to_date or "")
    if cur_col is None:
        return [_r("info", _LINK, name, "در گزارش جاری ستونی با تاریخ دورهٔ گزارش اصلی پیدا نشد")]
    prior_rows = index_rows(prior.row_code_df)
    cur_rows = index_rows(cash.row_code_df)
    prior_col = (data_columns(column_layout(prior.raw)) or [None])[0]
    if prior_col is None:
        return [_r("info", _LINK, name, "ستون داده‌ی گزارش اصلی پیدا نشد")]
    out = []
    for label, token in (("ابتدای دوره", ("موجودینقد", "ابتدا")), ("تغییر خالص", None), ("پایان دوره", ("موجودینقد", "پایان"))):
        if token is None:
            cur_row = find_tokens(cur_rows, "موجودینقد", "افزایش", exclude=("ابتدا", "پایان")) or find_tokens(cur_rows, "موجودینقد", "کاهش", exclude=("ابتدا", "پایان"))
            old_row = find_tokens(prior_rows, "موجودینقد", "افزایش", exclude=("ابتدا", "پایان")) or find_tokens(prior_rows, "موجودینقد", "کاهش", exclude=("ابتدا", "پایان"))
        else:
            cur_row = find_tokens(cur_rows, *token)
            old_row = find_tokens(prior_rows, *token)
        if cur_row is None or old_row is None:
            continue
        new_v, old_v = cur_row["vals"].get(cur_col), old_row["vals"].get(prior_col)
        if new_v is None or old_v is None:
            continue
        text = f"{label}: تجدیدارائه‌شده {new_v:,.0f} ↔ گزارش اصلی {old_v:,.0f}"
        out.append(_r("ok" if _close(new_v, old_v) else "warn", _LINK, name,
                      text if _close(new_v, old_v) else text + " -- تجدیدارائه این مقدار را تغییر داده است"))
    return out or [_r("info", _LINK, name, "ردیف‌های قابل مقایسه پیدا نشد")]


def _check_equity_balance(equity: Any, bal: Any) -> list[CheckResult]:
    """مانده‌ی پایان حقوق مالکانه در صورت تغییرات = جمع حقوق مالکانه در ترازنامه‌ی همان تاریخ.

    فقط وقتی مقایسه می‌شود که هر دو از یک نوع باشند (هر دو تلفیقی یا هر دو غیرتلفیقی).
    """
    name = "مانده‌ی پایان حقوق مالکانه: تغییرات حقوق مالکانه ↔ ترازنامه"
    if bool(equity.is_consolidated) != bool(bal.is_consolidated):
        return [_r("info", _LINK, name, "صورت تغییرات حقوق مالکانه و ترازنامه هم‌نوع نیستند "
                   "(یکی تلفیقی، یکی غیرتلفیقی)؛ مقایسه انجام نشد")]
    date = str(equity.period_end_to_date or "")
    eq_layout = column_layout(equity.raw)
    total_cols = [s for s, e in eq_layout.items() if "جمعکل" in row_key(e["header"])]
    if not date or not total_cols:
        return [_r("info", _LINK, name, "تاریخ پایان یا ستون «جمع کل» در صورت تغییرات حقوق مالکانه پیدا نشد")]
    total_col = max(total_cols)
    fa_date = fa_digits(date)
    closing = None
    for row in index_rows(equity.row_code_df):
        label = row["label"]
        if "مانده" in row_key(label) and "تجدید" not in row_key(label) and (date in label or fa_date in label):
            closing = row  # ردیفِ «مانده در پایان دوره»
    bal_layout = column_layout(bal.raw)
    bal_col = next((s for s in data_columns(bal_layout) if period_of(bal_layout, s) == date), None)
    bal_total = find_exact(index_rows(bal.row_code_df), row_key("جمع حقوق مالکانه"))
    if closing is None or bal_col is None or bal_total is None or bal_total["vals"].get(bal_col) is None:
        return [_r("info", _LINK, name, "ردیف مانده‌ی پایان دوره یا جمع حقوق مالکانه‌ی ترازنامه در همان تاریخ پیدا نشد")]
    eq_value = closing["vals"].get(total_col)
    if eq_value is None:
        return [_r("info", _LINK, name, "مقدار ستون «جمع کل» در ردیف مانده‌ی پایان دوره پیدا نشد")]
    bal_value = bal_total["vals"][bal_col]
    text = f"{fa_date}: مانده‌ی پایان {eq_value:,.0f} ↔ جمع حقوق مالکانه ترازنامه {bal_value:,.0f}"
    return [_r("ok" if _close(eq_value, bal_value) else "warn", _LINK, name, text)]


def analyze_set(reports: dict[str, Any], *, prior: Any | None = None) -> list[CheckResult]:
    """هم‌خوانی‌های بین صورت‌های یک اطلاعیه (و در صورت وجود، گزارش اصلی سال قبل)."""
    out: list[CheckResult] = []
    bal, cash = reports.get("balance"), reports.get("cash_flow")
    income, comp = reports.get("income"), reports.get("comprehensive")
    equity = reports.get("equity")
    if bal is not None and cash is not None:
        out.extend(_check_cash_links(bal, cash))
        out.extend(_check_dividends(bal, cash))
    if income is not None and bal is not None and cash is not None:
        out.extend(_check_retained_earnings(income, bal, cash))
    if income is not None and cash is not None:
        out.extend(_check_profit_cash(income, cash))
    if income is not None and comp is not None:
        out.extend(_check_comprehensive(income, comp))
    if equity is not None and bal is not None:
        out.extend(_check_equity_balance(equity, bal))
    if cash is not None:
        out.extend(_check_restatement(cash, prior))
    return out


# ---------------------------------------------------------------- صندوق
def analyze_portfolio(report: Any) -> list[CheckResult]:
    """صورت پورتفوی صندوق: جدول، و اگر ستون درصد باشد، جمع درصدها (بدون ردیف «جمع» که دوباره‌شماری می‌کند)."""
    section = "صندوق: صورت پورتفو"
    grid = getattr(report, "grid_df", None)
    if grid is None or len(grid) == 0:
        return [_r("error", section, "جدول پورتفو", "جدول پورتفو خالی است")]
    out = [_r("ok", section, "جدول پورتفو", f"{len(grid)} ردیف × {len(grid.columns)} ستون")]
    pct_cols = [c for c in grid.columns if "درصد" in row_key(str(c))]
    if not pct_cols:
        out.append(_r("info", section, "جمع درصدها", "ستون درصد در جدول پیدا نشد؛ جمع درصدها بررسی نشد"))
        return out
    pct = pct_cols[0]
    label_col = grid.columns[0]
    numeric = []
    for _, row in grid.iterrows():
        if "جمع" in row_key(str(row[label_col])):
            continue
        value = to_number(row[pct])
        if value is not None:
            numeric.append(value)
    if not numeric:
        out.append(_r("info", section, "جمع درصدها", f"ستون «{pct}» عدد ندارد"))
        return out
    total = sum(numeric)
    if abs(total - 100.0) <= 0.5:
        out.append(_r("ok", section, "جمع درصدها", f"جمع درصدها {total:.2f}٪"))
    elif abs(total - 100.0) <= 5.0:
        out.append(_r("info", section, "جمع درصدها", f"جمع درصدها {total:.2f}٪ (کمی با ۱۰۰ فرق دارد؛ شاید گرد کردن یا وجه نقد)"))
    else:
        out.append(_r("warn", section, "جمع درصدها", f"جمع درصدها {total:.2f}٪ با ۱۰۰ خیلی فاصله دارد"))
    return out
