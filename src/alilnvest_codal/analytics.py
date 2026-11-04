"""پاک‌سازی، مدل‌سازی، export و مقایسه‌ی خروجی‌ها -- لایه‌یِ کمکیِ اختیاری، بالایِ خروجیِ خامِ reports.py."""

from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd

from .models import BoardMember, ReportQuality, SubsidiaryCompany
from .utils import normalize_fa

# نگاشتِ اسمِ ستون‌هایِ فارسیِ رایج (تویِ شیت‌هایِ «شرکت های فرعی»/«شرکت های
# وابسته») به اسمِ فیلدِ انگلیسیِ SubsidiaryCompany -- برایِ standardize_columns.
SUBSIDIARY_COLUMN_ALIASES = {
    "نماد شرکت": "symbol",
    "نام شرکت": "name",
    "نماد ثبت شده نزد سازمان": "registered_symbol",
    "وضعیت ثبت شده نزد سازمان": "registration_status",
    "نوع فعالیت": "activity_type",
    "حسابرس": "auditor",
    "سال مالی": "fiscal_year",
    "فعال/غیر فعال": "active_status",
    "وضعیت": "status",
}


def _unique_columns(columns: list[Any]) -> list[str]:
    """نام ستون‌ها را پایدار و یکتا می‌کند تا ستون تکراری بی‌صدا ادغام نشود."""
    seen: dict[str, int] = {}
    result: list[str] = []
    for value in columns:
        base = str(value).strip() or "column"
        count = seen.get(base, 0)
        seen[base] = count + 1
        result.append(base if count == 0 else f"{base}_{count + 1}")
    return result


def clean_table(df: pd.DataFrame, *, drop_header: bool = True) -> tuple[pd.DataFrame, ReportQuality]:
    """
    خروجیِ خامِ grid_df (که address-based ـه، نه row_code) رو تمیز می‌کنه:
    ستون‌هایِ کاملاً خالی و ردیف‌هایِ کاملاً تکراری رو حذف می‌کنه، و اگه
    drop_header=True بود و ردیفِ اول با حداقل ۲ تا از اسم‌هایِ شناخته‌شده‌ی
    SUBSIDIARY_COLUMN_ALIASES مطابقت داشت، همون ردیف رو به‌عنوانِ هدرِ
    واقعی برمی‌داره (نه ستون‌هایِ عددیِ پیش‌فرضِ 0/1/2/...).

    خروجیِ دوم (ReportQuality) شفاف می‌گه چقدر ردیف/ستون حذف شد -- تا کاربر
    کورکورانه به تمیزکاری اعتماد نکنه.
    """
    out = df.copy()
    original_column_count = len(out.columns)
    duplicate_rows = int(out.duplicated().sum())

    out = out.dropna(axis=1, how="all").drop_duplicates().reset_index(drop=True)

    if drop_header and not out.empty:
        first_row_normalized = [normalize_fa(str(x)) for x in out.iloc[0].tolist()]
        known_headers_normalized = {normalize_fa(k) for k in SUBSIDIARY_COLUMN_ALIASES}
        if len(set(first_row_normalized) & known_headers_normalized) >= 2:
            out.columns = _unique_columns(list(out.iloc[0]))
            out = out.iloc[1:].reset_index(drop=True)

    quality = ReportQuality(
        "cleaned_table",
        duplicate_rows_removed=duplicate_rows,
        empty_columns_removed=original_column_count - len(out.columns),
    )
    return out, quality


def standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """ستون‌هایی که اسمِ فارسیِ شناخته‌شده دارن (SUBSIDIARY_COLUMN_ALIASES) رو به اسمِ فیلدِ انگلیسی تغییر می‌ده؛ بقیه دست‌نخورده می‌مونن."""
    out = df.copy()
    aliases_normalized = {normalize_fa(k): v for k, v in SUBSIDIARY_COLUMN_ALIASES.items()}
    out.columns = _unique_columns([aliases_normalized.get(normalize_fa(str(c)), str(c)) for c in out.columns])
    return out


def subsidiary_models(df: pd.DataFrame) -> list[SubsidiaryCompany]:
    """grid_dfِ تمیزشده‌یِ شرکت‌هایِ فرعی/وابسته رو به لیستی از SubsidiaryCompany تبدیل می‌کنه."""
    out = standardize_columns(df)
    allowed_fields = set(SubsidiaryCompany.__dataclass_fields__) - {"raw"}
    result: list[SubsidiaryCompany] = []
    for _, row in out.iterrows():
        values = {
            field_name: (None if field_name not in row or pd.isna(row[field_name]) else str(row[field_name]))
            for field_name in allowed_fields
        }
        result.append(SubsidiaryCompany(**values, raw=row.to_dict()))
    return result


def board_models(df: pd.DataFrame) -> list[BoardMember]:
    """
    grid_dfِ تمیزشده‌یِ اعضایِ هیئت‌مدیره رو به لیستی از BoardMember تبدیل
    می‌کنه. اگه ستونِ "name"/"role" (بعدِ standardize_columns) پیدا نشد،
    به‌ترتیب اولین/دومین ستون رو به‌عنوانِ fallback به‌کار می‌بره -- چون
    اسمِ دقیقِ ستون‌هایِ این شیت هنوز تویِ SUBSIDIARY_COLUMN_ALIASES نیستن.
    """
    out = standardize_columns(df)
    columns = list(out.columns)
    name_column = "name" if "name" in columns else (columns[0] if columns else None)
    role_column = "role" if "role" in columns else (columns[1] if len(columns) > 1 else None)

    result: list[BoardMember] = []
    for _, row in out.iterrows():
        name = None if name_column is None or pd.isna(row.get(name_column)) else str(row.get(name_column))
        role = None if role_column is None or pd.isna(row.get(role_column)) else str(row.get(role_column))
        result.append(BoardMember(name, role, row.to_dict()))
    return result


def dataframe_to_json(df: pd.DataFrame, path: str, *, metadata: dict | None = None, **kwargs: Any) -> None:
    """DataFrame رو به یه فایلِ JSON با ساختارِ {"metadata": ..., "rows": [...]} می‌نویسه (UTF-8، بدونِ escapeِ فارسی)."""
    def json_safe(value: Any) -> Any:
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if isinstance(value, dict):
            return {str(k): json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [json_safe(v) for v in value]
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
        return str(value)

    rows = df.where(pd.notna(df), None).to_dict(orient="records")
    payload = {"metadata": json_safe(metadata or {}), "rows": json_safe(rows)}
    kwargs.setdefault("ensure_ascii", False)
    kwargs.setdefault("indent", 2)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, **kwargs)


def compare_reports(old: Any, new: Any, *, key: str | None = None) -> dict[str, Any]:
    """
    دو گزارش (یا مستقیم دو DataFrame) رو مقایسه می‌کنه -- `old`/`new` می‌تونن
    خودِ FinancialReport/PortfolioReport باشن (grid_df/row_code_df ازشون
    خونده می‌شه) یا مستقیم DataFrame.

    اگه key بدی و تویِ هر دو ستون باشه، بر اساسِ اون ایندکس می‌کنه (مقایسه‌ی
    دقیق‌تر بر اساسِ شناسه، نه شماره‌ی ردیف). خروجی: کدوم ردیف‌ها اضافه
    شدن، کدوم حذف شدن، کدوم مقدارشون فرق کرده.
    """
    old_df = getattr(old, "grid_df", getattr(old, "row_code_df", old)).copy()
    new_df = getattr(new, "grid_df", getattr(new, "row_code_df", new)).copy()

    if key and key in old_df.columns and key in new_df.columns:
        old_df, new_df = old_df.set_index(key), new_df.set_index(key)
    if not (old_df.index.is_unique and new_df.index.is_unique):
        # با ایندکسِ تکراری، loc یک DataFrame برمی‌گرداند نه یک سطر؛ مقایسه بی‌معنی می‌شود.
        raise ValueError("compare_reports: شناسه‌ها باید یکتا باشند (ردیفِ تکراری در یکی از گزارش‌ها).")

    old_keys, new_keys = set(old_df.index), set(new_df.index)

    changed: list[dict[str, Any]] = []
    for idx in sorted(old_keys & new_keys, key=str):  # ترتیبِ قطعی برای خروجیِ قابل‌مقایسه
        if not old_df.loc[idx].equals(new_df.loc[idx]):
            changed.append({"key": idx, "old": old_df.loc[idx].to_dict(), "new": new_df.loc[idx].to_dict()})

    added_keys = sorted(new_keys - old_keys, key=str)
    removed_keys = sorted(old_keys - new_keys, key=str)
    return {
        # reset_index تا کلیدِ ردیف (همان «key» در changed) در خروجی بماند؛ با to_dict("records") خالی گم می‌شد
        "added": new_df.loc[added_keys].rename_axis("key").reset_index().to_dict("records"),
        "removed": old_df.loc[removed_keys].rename_axis("key").reset_index().to_dict("records"),
        "changed": changed,
    }
