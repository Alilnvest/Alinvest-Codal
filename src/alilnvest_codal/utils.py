"""کمک‌تابع‌های عمومی: نرمال‌سازیِ متنِ فارسی/عربی، تاریخِ جلالی و دسترسی به دیکشنری."""

from __future__ import annotations

import re
from typing import Any

# کاراکترهای نامرئی و جهت‌دهنده که در HTML/متنِ کدال دیده می‌شوند و تطابق را خراب می‌کنند.
_INVISIBLE = (
    "\u200b", "\u200c", "\u200d", "\u200e", "\u200f", "\ufeff", "\u00ad", "\u0640",
    "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",
)
_CHAR_MAP_DICT: dict[str, str] = {
    "ي": "ی",
    "ى": "ی",
    "ك": "ک",
    "ۀ": "ه",
    "ة": "ه",
    "آ": "ا",
    **{ch: "" for ch in _INVISIBLE},
    # ارقامِ عربی-هندی به ارقامِ فارسی
    **dict(zip("٠١٢٣٤٥٦٧٨٩", "۰۱۲۳۴۵۶۷۸۹")),
}
_CHAR_MAP = str.maketrans(_CHAR_MAP_DICT)


def normalize_fa(text: str | None) -> str:
    """یکدست‌سازیِ متنِ فارسی/عربی برای *مقایسه* (نه نمایش): حروفِ مشابه، ارقام، و همه‌ی فاصله‌ها حذف می‌شوند."""
    if not text:
        return ""
    return "".join(str(text).translate(_CHAR_MAP).split())


def is_consolidated_title(title: str | None) -> bool:
    """آیا عنوانِ اطلاعیه «تلفیقی» است؟ (نه «غیرتلفیقی»؛ که شامل «تلفیقی» هم هست)."""
    norm = normalize_fa(title)
    return "تلفیقی" in norm and "غیرتلفیقی" not in norm


_ALIAS_STRIP = re.compile(r"[^0-9a-z]+")


def normalize_alias(alias: str | None) -> str:
    """aliasNameِ کدال برای مقایسه: حروف کوچک، بدون خط تیره/فاصله/زیرخط.

    کدال یک شیت را در قالب‌های مختلف با املای متفاوت می‌فرستد؛ مثلاً
    «InterpretativeReportSummaryPage1» در قالب تولیدی و «InterpretativeReportSummary-Page1» در قالب بانکی.
    """
    if not alias:
        return ""
    return _ALIAS_STRIP.sub("", str(alias).lower())


def is_consolidated_alias(alias: str | None) -> bool | None:
    """از روی aliasName: True اگر تلفیقی، False اگر غیرتلفیقی، None اگر alias نامعلوم باشد."""
    norm = normalize_alias(alias)
    if not norm:
        return None
    return norm.startswith("consolidated")


_TRUE_STRINGS = frozenset({"true", "1", "yes", "y", "t"})


def to_bool(value: Any) -> bool:
    """مقدارِ بولی‌مانندِ API را درست می‌خواند؛ مثلاً "false" و "0" یعنی False (نه True)."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in _TRUE_STRINGS


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    """تبدیلِ میلادی به جلالی بدونِ وابستگی (الگوریتمِ رایجِ jalaali-js؛ برای ۱۹۰۰ تا ۲۱۰۰ دقیق است)."""
    g_d_m = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) + ((gy2 + 399) // 400) + gd + g_d_m[gm - 1]
    jy = -1595 + (33 * (days // 12053))
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + (days // 31), 1 + (days % 31)
    else:
        jm, jd = 7 + ((days - 186) // 30), 1 + ((days - 186) % 30)
    return jy, jm, jd


def jalali_month_days(year: int, month: int) -> int:
    """تعدادِ روزهای یک ماهِ جلالی (قاعده‌ی ۳۳ساله؛ برای سال‌های ۱۳۴۳ تا ۱۴۷۲ با تقویمِ رسمی یکی است)."""
    if month <= 6:
        return 31
    if month <= 11:
        return 30
    return 30 if year % 33 in {1, 5, 9, 13, 17, 22, 26, 30} else 29


def today_jalali() -> str:
    """امروز به تقویم جلالی و به وقتِ تهران (نه ساعتِ سرور).

    روی سرورِ خارج از ایران (UTC) بینِ ۰۰:۰۰ تا ۰۳:۳۰ تهران، تاریخِ سرور یک روز عقب است و
    جست‌وجوی «تا امروز» اطلاعیه‌های همان روز را جا می‌انداخت.
    """
    import datetime as _dt

    try:
        from zoneinfo import ZoneInfo

        now = _dt.datetime.now(ZoneInfo("Asia/Tehran"))
    except Exception:  # zoneinfo/tzdata در دسترس نیست (مثلاً بعضی نصب‌های Termux/ویندوز)
        now = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=3, minutes=30)))
    jy, jm, jd = gregorian_to_jalali(now.year, now.month, now.day)
    return f"{jy:04d}/{jm:02d}/{jd:02d}"


_JDATE_RE = re.compile(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$")


def validate_jdate(value: str, *, param_name: str = "date") -> str:
    """فرمت و معتبربودنِ یک تاریخِ جلالی (yyyy/mm/dd یا yyyy-mm-dd) را بررسی و شکلِ نرمال را برمی‌گرداند."""
    m = _JDATE_RE.match((value or "").strip())
    if not m:
        raise ValueError(f"{param_name}={value!r} فرمتش درست نیست -- باید yyyy/mm/dd یا yyyy-mm-dd باشه.")
    year, month, day = (int(g) for g in m.groups())
    try:
        import jdatetime

        jdatetime.date(year, month, day)
    except ImportError:
        # jdatetime نصب نیست: طولِ واقعیِ ماه با قاعده‌ی ۳۳ساله چک می‌شود.
        if not (1 <= month <= 12 and 1 <= day <= jalali_month_days(year, month)):
            raise ValueError(f"{param_name}={value!r} ماه/روزش خارج از محدوده‌ست.") from None
    except ValueError as exc:
        raise ValueError(f"{param_name}={value!r} یه تاریخِ جلالیِ معتبر نیست: {exc}") from exc
    return f"{year:04d}/{month:02d}/{day:02d}"


def get_ci(data: dict, *keys: str) -> Any:
    """مقدارِ اولین کلیدِ موجود را برمی‌گرداند؛ کلیدهای کدال گاهی camelCase و گاهی PascalCase هستند."""
    if not isinstance(data, dict):
        return None
    for key in keys:
        if key in data:
            return data[key]
    lower = {str(k).lower(): v for k, v in data.items()}
    for key in keys:
        if key.lower() in lower:
            return lower[key.lower()]
    return None


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    s = str(value).strip()
    return s == "" or s.lower() == "nan"


_FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_AR_DIGITS = "٠١٢٣٤٥٦٧٨٩"  # ارقام عربی-هندی؛ کدال گاهی آن‌ها را هم در عنوان می‌گذارد
_ASCII_DIGITS = "0123456789"
_DIGIT_MAP = str.maketrans(_FA_DIGITS + _AR_DIGITS, _ASCII_DIGITS * 2)
_PERIOD_RE = re.compile(r"([\d۰-۹٠-٩]{4}/[\d۰-۹٠-٩]{2}/[\d۰-۹٠-٩]{2})")


def extract_period_end(title: str | None) -> str | None:
    """تاریخِ «منتهی به ...» را از عنوانِ اطلاعیه درمی‌آورد (وقتی متادیتای ساختاریافته نیست)."""
    if not title:
        return None
    m = _PERIOD_RE.search(title)
    if not m:
        return None
    return m.group(1).translate(_DIGIT_MAP)
