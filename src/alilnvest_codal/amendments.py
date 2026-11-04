"""اصلاحیه: اطلاعیه‌ی اصلاح‌شده را با اولیه‌اش می‌سنجد و می‌گوید اصلاحیه کدام عددها را عوض کرده است.

کدال گاهی یک اطلاعیه را «اصلاحیه» می‌کند و هر دو نسخه جداگانه منتشر می‌شوند. این ماژول نسخه‌ی اولیه‌ی همان دوره
را پیدا می‌کند (همان پایان دوره، همان نوع تلفیقی یا غیرتلفیقی، و بدون عبارت «اصلاحیه») و عددهایی را که فرق دارند
فهرست می‌کند. این ماژول خالص است؛ جست‌وجوی شبکه در audit.py انجام می‌شود.
"""

from __future__ import annotations

from typing import Any, Iterable

from .checks import CheckResult, column_layout, fa_digits, index_rows, period_of, row_key

__all__ = ["AMENDMENT_MARK", "is_amendment", "find_original", "differences", "compare_with_original"]

AMENDMENT_MARK = "اصلاحیه"
TOLERANCE = 1.0  # ریال
KIND_NAMES = {"balance": "ترازنامه", "income": "سود و زیان", "cash_flow": "جریان نقد"}


def is_amendment(report: Any) -> bool:
    return AMENDMENT_MARK in row_key(getattr(report, "title", "") or "")


def find_original(current: Any, candidates: Iterable[Any]) -> Any | None:
    """اولین اطلاعیه‌ای که همان پایان دوره و همان نوع (تلفیقی/غیرتلفیقی) را دارد و اصلاحیه نیست."""
    matches = []
    for rep in candidates:
        if rep is None or rep.tracing_no == current.tracing_no or is_amendment(rep):
            continue
        if rep.period_end_to_date != current.period_end_to_date:
            continue
        if bool(rep.is_consolidated) != bool(current.is_consolidated):
            continue
        matches.append(rep)
    if not matches:
        return None
    def sort_key(rep):
        date = getattr(rep, "publish_date_time", None) or getattr(rep, "sent_date_time", None) or "~"
        return (date, int(rep.tracing_no) if rep.tracing_no is not None else 2**63 - 1)
    return min(matches, key=sort_key)


def _values(report: Any) -> dict[tuple[str, str], tuple[str, float]]:
    """(کلید برچسب، تاریخ ستون) -> (برچسب، مقدار). اولین رخداد هر کلید نگه داشته می‌شود."""
    layout = column_layout(report.raw)
    out: dict[tuple[str, str], tuple[str, float]] = {}
    for row in index_rows(report.row_code_df):
        if not row["key"]:
            continue
        for col, value in row["vals"].items():
            if col <= 1:
                continue
            date = period_of(layout, col)
            if date:
                out.setdefault((row["key"], date), (row["label"], value))
    return out


def differences(current: Any, original: Any, *, tolerance: float = TOLERANCE) -> list[dict[str, Any]]:
    """عددهایی که در اصلاحیه (current) با اطلاعیه‌ی اولیه (original) فرق دارند."""
    new, old = _values(current), _values(original)
    diffs = []
    for key in sorted(set(new) & set(old), key=lambda k: (k[1], k[0])):
        label, before = old[key]
        _, after = new[key]
        if abs(before - after) > tolerance:
            diffs.append({"label": label, "period": key[1], "original": before, "amended": after})
    return diffs


def compare_with_original(kind: str, current: Any, original: Any) -> CheckResult:
    name = f"{KIND_NAMES.get(kind, kind)}: اصلاحیه در برابر اطلاعیه‌ی اولیه"
    head = f"TracingNo={current.tracing_no} (اصلاحیه) در برابر TracingNo={original.tracing_no} (اولیه)"
    diffs = differences(current, original)
    if not diffs:
        return CheckResult("ok", "اصلاحیه", name, f"{head}: هیچ عددی تغییر نکرده است")
    details = [f"{d['label']} | {fa_digits(d['period'])}: {d['original']:,.0f} → {d['amended']:,.0f}"
               for d in diffs[:15]]
    if len(diffs) > 15:
        details.append(f"و {len(diffs) - 15} مورد دیگر")
    return CheckResult("info", "اصلاحیه", name, f"{head}: {len(diffs)} عدد تغییر کرده است", tuple(details))
