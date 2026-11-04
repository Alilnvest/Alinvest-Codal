"""تبدیلِ سلول‌هایِ خامِ کدال به pandas.DataFrame -- دو روش برایِ دو نوع جدول."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from .utils import get_ci, is_blank

_ADDR_RE = re.compile(r"^([A-Za-z]+)(\d+)$")


def _col_letters_to_num(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n


def _row_sequence(cell: dict) -> int:
    """شماره‌ی سطر واقعی را از sequence یا address، با نرمال‌سازی مقادیر رشته‌ای، می‌گیرد."""
    seq = get_ci(cell, "rowSequence", "row_sequence")
    try:
        if seq is not None:
            return int(float(str(seq).strip()))
    except (TypeError, ValueError):
        pass
    digits = "".join(ch for ch in str(get_ci(cell, "address") or "") if ch.isdigit())
    return int(digits) if digits else 0


def cells_to_dataframe(cells: list[dict], *, drop_empty_rows: bool = True) -> pd.DataFrame:
    """
    برایِ صورت‌هایِ مالی (سودوزیان/ترازنامه/جریانِ نقد): ایندکس = row_code.

    نکته‌ی مهم: row_code در یک گزارش یکتا نیست. مثلاً در فملی کدِ ۲۱ هم برایِ
    «سود (زیان) خالص عملیات متوقف شده» و هم برایِ «سود پایه هر سهم» به کار رفته، و کدِ ۰
    برایِ سرستون و یک عنوانِ بخش. نسخه‌های قبلی این سطرها را بی‌صدا روی هم می‌نوشتند و
    سطرِ واقعی (و سرستون‌های دوره) از بین می‌رفت. حالا سطرها با rowSequence (شماره‌ی سطرِ
    واقعیِ شیت، همان سطرِ آدرسِ A20 و غیره) جدا می‌شوند. اولین سطرِ هر row_code همان عددِ
    صحیح می‌ماند تا سازگاری حفظ شود؛ تکرارها با جزءِ کسریِ کوچک متمایز می‌شوند (۲۱ و ۲۱٫۰۰۱).
    نگاشتِ ایندکس به شماره‌ی سطر در df.attrs["row_sequence"] نگه داشته می‌شود.

    drop_empty_rows=True: ردیف‌هایی که همه‌ی ستون‌های داده‌شون خالی‌ان حذف می‌شن؛ این‌ها معمولاً
    عنوانِ بخش یا متنِ توضیحی‌اند، نه یک سطرِ مالیِ واقعی.
    """
    by_key: dict[tuple, dict[int, Any]] = {}
    for c in cells:
        rc = get_ci(c, "rowCode", "row_code")
        col = get_ci(c, "columnSequence", "column_sequence")
        if rc is None or col is None:
            continue
        try:
            rc_key = int(float(str(rc).strip()))
        except (TypeError, ValueError):
            rc_key = rc
        try:
            col_key = int(float(str(col).strip()))
        except (TypeError, ValueError):
            continue
        by_key.setdefault((rc_key, _row_sequence(c)), {})[col_key] = get_ci(c, "value")

    if not by_key:
        return pd.DataFrame()

    sequences_by_code: dict[Any, list[int]] = {}
    for rc, seq in by_key:
        sequences_by_code.setdefault(rc, []).append(seq)
    index_of: dict[tuple, Any] = {}
    for rc, seqs in sequences_by_code.items():
        for k, seq in enumerate(sorted(set(seqs))):
            index_of[(rc, seq)] = rc if k == 0 else rc + k / 1000.0

    rows = {index_of[key]: values for key, values in by_key.items()}
    df = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    df.index.name = "row_code"
    df = df.reindex(sorted(df.columns), axis=1)
    df.attrs["row_sequence"] = {index_of[key]: key[1] for key in by_key}

    if drop_empty_rows and df.shape[1] > 1:
        data_cols = df.columns[1:]  # ستونِ اول معمولاً برچسبِ ردیف/شرح‌ـه
        all_blank = df[data_cols].apply(lambda row: all(is_blank(v) for v in row), axis=1)
        df = df.loc[~all_blank]

    return df


def cells_to_grid(cells: list[dict], *, header_row: int | None = 1) -> pd.DataFrame:
    """
    برایِ جدول‌هایِ لیستی (مثلِ صورت‌وضعیتِ پورتفویِ صندوق) که سرفصلِ ثابت ندارن:
    شبکه رو مستقیم از آدرسِ سلول‌ها (A1, B2, ...) می‌سازه.
    header_row: اگه بدی، اون سطر به‌عنوانِ اسمِ ستون‌ها استفاده می‌شه و از داده حذف می‌شه.

    ⚠️ این تابع برایِ صورتِ پورتفویِ صندوق تست‌شده روی داده‌یِ واقعی نیست -- ساختارش
    رو با یه نمونه‌یِ واقعی چک کن و اگه لازم بود تنظیمش کن.
    """
    grid: dict[int, dict[int, Any]] = {}
    for c in cells:
        addr = get_ci(c, "address")
        val = get_ci(c, "value")
        if not addr:
            continue
        m = _ADDR_RE.match(str(addr))
        if not m:
            continue
        col_num = _col_letters_to_num(m.group(1))
        row_num = int(m.group(2))
        grid.setdefault(row_num, {})[col_num] = val

    if not grid:
        return pd.DataFrame()

    df = pd.DataFrame.from_dict(grid, orient="index").sort_index()
    df = df.reindex(sorted(df.columns), axis=1)

    if header_row is not None and header_row in df.index:
        header_vals = df.loc[header_row]
        df = df.drop(index=header_row)
        columns = [
            str(v) if not is_blank(v) else f"col_{i}" for i, v in enumerate(header_vals, start=1)
        ]
        seen: dict[str, int] = {}
        unique: list[str] = []
        for name in columns:
            count = seen.get(name, 0)
            seen[name] = count + 1
            unique.append(name if count == 0 else f"{name}_{count + 1}")
        df.columns = unique

    return df


def apply_row_code_map(df: pd.DataFrame, mapping: dict[int, str]) -> pd.DataFrame:
    """ردیف‌هایِ row_code رو به نام‌هایِ استانداردِ خودت map می‌کنه؛ ردیف‌هایِ نگاشت‌نشده حذف می‌شن."""
    out = df.loc[df.index.isin(list(mapping))].copy()
    out.index = out.index.map(mapping)
    return out
