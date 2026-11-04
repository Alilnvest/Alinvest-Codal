"""ابزارهای سبک برای نمایش پیشرفت جست‌وجو و دریافت گزارش‌های کدال."""

from __future__ import annotations

import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TextIO


@dataclass(frozen=True)
class SearchProgress:
    """وضعیت یک مرحله از جست‌وجوی صفحه‌به‌صفحهٔ کدال."""

    phase: str
    page: int
    page_count: int
    scanned: int
    yielded: int
    current_title: str | None = None
    current_date: str | None = None
    done: bool = False


ProgressCallback = Callable[[SearchProgress], None]


def _jalali_year(value: str | None) -> str:
    if not value:
        return "----"
    digits = "0123456789۰۱۲۳۴۵۶۷۸۹"
    translated = value.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", digits[:10]))
    return translated[:4] if len(translated) >= 4 else "----"


class ConsoleProgress:
    """نمایشگر کم‌هزینهٔ پیشرفت؛ خروجی را روی stderr می‌نویسد.

    اگه stream یه ترمینالِ تعاملی (TTY) باشه، یه نوارِ پیشرفتِ تک‌خطی با
    ``\\r`` نمایش داده می‌شه (رفتارِ کلاسیک). ولی وقتی خروجی pipe/redirect
    شده باشه یا لاگ بشه (مثلاً داخلِ یه ربات که stdout رو journalctl/فایلِ
    لاگ می‌گیره)، ``\\r`` هیچ اثری نداره و همه‌ی به‌روزرسانی‌ها پشتِ سرِ هم
    روی یه خط چسبیده و نامفهوم می‌شن. برایِ این حالت، به‌جایِ نوارِ گرافیکی،
    فقط هر ``line_every`` موردِ بررسی‌شده (یا موقعِ اتمام) یه خطِ ساده و
    مستقلِ جدید چاپ می‌شه -- خروجی همیشه تمیز می‌مونه، فارغ از اینکه
    مصرف‌کننده‌ی لاگ چی باشه.
    """

    def __init__(
        self,
        *,
        stream: TextIO | None = None,
        label: str = "کدال",
        min_interval: float = 0.25,
        line_every: int = 5,
    ) -> None:
        self.stream = stream or sys.stderr
        self.label = label
        self.started_at = time.monotonic()
        self.min_interval = max(0.0, min_interval)
        self.line_every = max(1, line_every)
        self._last_render_at = 0.0
        self._last_line_scanned = 0
        self._closed = False
        self._is_tty = bool(getattr(self.stream, "isatty", lambda: False)())

    def __call__(self, event: SearchProgress) -> None:
        if self._closed:
            return
        if self._is_tty:
            self._render_bar(event)
        else:
            self._render_line(event)

    def _terminal_width(self) -> int:
        # یه ستون کمتر از عرضِ واقعی: نوشتنِ تا آخرین ستون باعثِ wrap می‌شه.
        try:
            columns = shutil.get_terminal_size((80, 24)).columns
        except (OSError, ValueError):
            columns = 80
        return max(20, columns - 1)

    def _render_bar(self, event: SearchProgress) -> None:
        now = time.monotonic()
        if not event.done and now - self._last_render_at < self.min_interval:
            return
        width = self._terminal_width()
        total = max(event.page_count, 1)
        percent = min(100, round(event.page * 100 / total))
        bar_width = min(24, max(8, width // 4))
        filled = round(bar_width * percent / 100)
        bar = "█" * filled + "░" * (bar_width - filled)
        elapsed = int(now - self.started_at)

        # بخشِ ضروری همیشه هست؛ بقیه فقط اگه توی عرضِ ترمینال جا بشه اضافه می‌شن.
        # اگه خط از عرضِ ترمینال بلندتر بشه، ترمینال wrap می‌کنه و \r فقط به
        # ابتدایِ همون ردیفِ آخر برمی‌گرده -- نتیجه: هر به‌روزرسانی یه خطِ
        # جدید پایین‌تر (باگی که کاربر دید).
        status = f"{self.label} [{bar}] {percent}% | بررسی {event.scanned} | موفق {event.yielded} | {elapsed}s"
        extras = [
            f"سال {_jalali_year(event.current_date)}",
            f"صفحه {event.page}/{total}",
            self._short_title(event.current_title),
        ]
        for extra in extras:
            if extra and len(status) + 3 + len(extra) <= width:
                status += f" | {extra}"
        status = status[:width]
        # \033[K = پاک‌کردنِ بقیه‌یِ خط (به‌جایِ ljust(220) که خودش باعثِ wrap می‌شد)
        print("\r" + status + "\033[K", end="", file=self.stream, flush=True)
        self._last_render_at = now
        if event.done:
            print(file=self.stream, flush=True)
            self._closed = True

    def _render_line(self, event: SearchProgress) -> None:
        # هر line_every موردِ بررسی‌شده (یا موقعِ اتمام) یه خطِ کاملِ جدید --
        # هیچ‌وقت با خطِ قبلی قاطی نمی‌شه، حتی وقتی خروجی لاگ/pipe بشه.
        is_milestone = event.scanned - self._last_line_scanned >= self.line_every
        if not (event.done or is_milestone):
            return
        year = _jalali_year(event.current_date)
        phase = self._phase_fa(event.phase)
        elapsed = int(time.monotonic() - self.started_at)
        line = (
            f"{self.label} | {phase} | بررسی {event.scanned} | موفق {event.yielded} "
            f"| سال {year} | {elapsed}s"
        )
        print(line, file=self.stream, flush=True)
        self._last_line_scanned = event.scanned
        if event.done:
            self._closed = True

    @staticmethod
    def _phase_fa(phase: str) -> str:
        return {
            "search": "جست‌وجو",
            "download": "دانلود",
            "report": "پردازش",
            "snapshot": "snapshot",
        }.get(phase, phase)

    @staticmethod
    def _short_title(raw: str | None) -> str:
        title = (raw or "").replace("\n", " ").strip()
        if len(title) > 32:
            title = title[:29] + "..."
        return title


def make_progress(
    progress: bool | ProgressCallback | None,
    *,
    label: str,
) -> ProgressCallback | None:
    """مقدار عمومی progress را به callback تبدیل می‌کند."""
    if progress is True:
        return ConsoleProgress(label=label)
    if callable(progress):
        return progress
    return None
