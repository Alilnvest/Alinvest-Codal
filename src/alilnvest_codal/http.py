"""لایه‌ی HTTP کتابخانه.

دو مسیرِ درخواست داریم:

* ``get()`` بر پایه‌ی requests (همه‌ی توابع اصلی از آن استفاده می‌کنند).
* ``CodalHTTPClient`` بر پایه‌ی httpx (برای کلاینتِ پایدار و async).

هر دو یک سیاستِ مشترک دارند: فاصله‌ی مؤدبانه بین درخواست‌ها، تشخیصِ صفحه‌ی بلاک حتی وقتی
status=200 است، retry فقط برای خطاهای گذرا (429/408/425/5xx/بلاک) و هیچ retry‌ای برای خطاهای
دائمی مثل 404 (که بلافاصله NetworkError می‌دهد و مدارِ قطع‌کننده را باز نمی‌کند).
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import threading
import time
from collections import OrderedDict
from time import perf_counter
from typing import Any

import requests
from requests.adapters import HTTPAdapter

from .cache import Cache
from .config import DEFAULT_CONFIG, DEFAULT_USER_AGENT, CodalConfig
from .endpoints import BROWSER_HEADERS
from .exceptions import CodalNotFoundError, NetworkError, RateLimitError

try:
    import httpx
except ImportError:  # pragma: no cover -- httpx فقط برای CodalHTTPClient لازم است
    httpx = None  # type: ignore[assignment]

logger = logging.getLogger("alilnvest_codal.http")
logger.addHandler(logging.NullHandler())  # کتابخانه نباید روی logging برنامه‌ی میزبان اثر بگذارد

# نشانه‌های صفحه‌ی بلاک/دسترسیِ محدود که گاهی با status=200 برمی‌گردند (پشت WAF).
_BLOCK_PAGE_MAX_BYTES = 20_000  # صفحه‌ی بلاک کوچک است؛ بزرگ‌تر از این بلاک حساب نمی‌شود
_BLOCK_MARKERS = ("دسترسی شما", "مسدود شده", "General Error Detected", "Access Denied")

HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7",
    # br عمداً نیست: اگر brotli نصب نباشد، پاسخِ br-شده به‌صورتِ بایت‌های خام می‌ماند.
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.codal.ir/",
    # keep-alive نمی‌گذاریم: سیستم ضدِ بات کدال به درخواست‌های پشت‌سرهم روی یک کانکشن حساس است.
    "Connection": "close",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
}

# تنظیماتِ پیش‌فرضِ ادب و retry؛ با configure() یا safe_mode()/fast_mode() قابل تغییرند.
def _env_delay(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, default)))
    except (TypeError, ValueError):
        return default


# کتابخانه به‌صورت پیش‌فرض هیچ مکثی ایجاد نمی‌کند؛ کنترل سرعت، retry و زمان‌بندی
# عمداً به برنامه‌ی میزبان واگذار شده است. برای اجرای محافظه‌کارانه می‌توان
# صریحاً safe_mode() یا configure(...) را صدا زد.
DEFAULT_MIN_DELAY = 0.0
DEFAULT_MAX_DELAY = 0.0
MIN_DELAY = _env_delay("CODAL_MIN_DELAY", DEFAULT_MIN_DELAY)
MAX_DELAY = max(MIN_DELAY, _env_delay("CODAL_MAX_DELAY", DEFAULT_MAX_DELAY))

# فاصله‌ی بین دو *نماد*: وقتی جست‌وجو از یک نماد به نمادِ دیگر می‌رود، حداقل این‌قدر از آخرین درخواستِ واقعیِ
# نمادِ قبلی گذشته باشد (تصادفی بین MIN و MAX). با CODAL_SYMBOL_MIN_DELAY / CODAL_SYMBOL_MAX_DELAY یا
# configure(symbol_min_delay=..., symbol_max_delay=...) عوض می‌شود؛ ۰ یعنی خاموش.
DEFAULT_SYMBOL_MIN_DELAY = 0.0
DEFAULT_SYMBOL_MAX_DELAY = 0.0
SYMBOL_MIN_DELAY = _env_delay("CODAL_SYMBOL_MIN_DELAY", DEFAULT_SYMBOL_MIN_DELAY)
SYMBOL_MAX_DELAY = max(SYMBOL_MIN_DELAY, _env_delay("CODAL_SYMBOL_MAX_DELAY", DEFAULT_SYMBOL_MAX_DELAY))
RATE_LIMIT_SLEEP = 0.0
MAX_RETRIES = 1
RETRYABLE_STATUS_CODES = frozenset({408, 425, 500, 502, 503, 504})
MAX_TOTAL_RETRY_DELAY_SECONDS = 30  # سقفِ مجموعِ زمانِ صبر برای یک درخواست
CIRCUIT_COOLDOWN_SECONDS = 0.0
# بعد از هر 429، فاصله‌ی بین *همه‌ی* درخواست‌ها برای مدتی بیشتر می‌شود (throttleِ تطبیقی).
# قبلاً بعد از 429 با همان سرعتِ قبلی ادامه می‌دادیم و جست‌وجوی بعدی هم 429 می‌گرفت.
RATE_LIMIT_MIN_INTERVAL = 0.0
RATE_LIMIT_PENALTY_SECONDS = 0.0

# کش GET با TTL و سقفِ تعداد (LRU). نکته: هر ورودی ممکن است چند صد کیلوبایت پاسخ باشد.
CACHE_ENABLED = True
CACHE_TTL_SECONDS = 6 * 60 * 60
CACHE_MAX_ENTRIES = 256

_session = requests.Session()
_session.headers.update(HEADERS)
_session.mount("https://", HTTPAdapter(pool_connections=20, pool_maxsize=20))
_session.mount("http://", HTTPAdapter(pool_connections=20, pool_maxsize=20))

_last_request_time = 0.0
_throttle_lock = threading.Lock()

# آخرین لحظه‌ای که یک پاسخِ *واقعی* از شبکه آمد (نه کش) و نمادِ جاری؛ برای فاصله‌ی بین نمادها.
_last_network_time = 0.0
_current_symbol: str | None = None
_symbol_lock = threading.Lock()

_circuit_blocked_until = 0.0
_circuit_lock = threading.Lock()

_rate_limited_until = 0.0

_cache: "OrderedDict[tuple, tuple[float, requests.Response]]" = OrderedDict()
_cache_lock = threading.Lock()


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f} ثانیه"
    return f"{seconds / 60:.1f} دقیقه"


def _full_jitter_backoff(base_seconds: float, attempt: int, cap_seconds: float) -> float:
    """بک‌آفِ نمایی با «جیترِ کامل» (توصیه‌ی AWS): تصادفی بین ۰ و سقفِ نمایی."""
    cap = min(base_seconds * (2 ** max(0, attempt - 1)), cap_seconds)
    return random.uniform(0.0, cap)


def _rate_limit_backoff(base_seconds: float, attempt: int, cap_seconds: float) -> float:
    """بک‌آف برای 429 با «جیترِ مساوی»: همیشه حداقل نصفِ سقفِ نمایی صبر می‌کند.

    جیترِ کامل می‌توانست نزدیکِ صفر باشد؛ یعنی بعد از 429 تقریباً بلافاصله دوباره درخواست
    می‌رفت و دوباره 429 می‌گرفت (همان خطایی که روی جست‌وجوی «آسیا» دیده شد).
    """
    cap = min(base_seconds * (2 ** max(0, attempt - 1)), cap_seconds)
    return cap / 2 + random.uniform(0.0, cap / 2)


def _note_rate_limited() -> None:
    """ثبتِ 429: تا RATE_LIMIT_PENALTY_SECONDS ثانیه، فاصله‌ی بین درخواست‌ها بیشتر می‌شود."""
    global _rate_limited_until
    with _circuit_lock:
        _rate_limited_until = time.monotonic() + RATE_LIMIT_PENALTY_SECONDS


def is_rate_limited() -> bool:
    """آیا در چند دقیقه‌ی اخیر 429 گرفته‌ایم (و هنوز در حالتِ آهسته‌ایم)؟"""
    return time.monotonic() < _rate_limited_until


def _cache_key(url: str, params: dict | None, headers: dict | None = None) -> tuple:
    """کلیدِ کش. مقدارِ list (مثلاً خروجیِ parse_qs) به tuple تبدیل می‌شود تا hashable باشد."""

    def _normalize(v: Any) -> Any:
        if isinstance(v, (list, tuple)):
            return tuple(str(x) for x in v)
        return str(v)

    items = tuple(sorted(((str(k), _normalize(v)) for k, v in (params or {}).items()), key=lambda kv: kv[0]))
    header_items = tuple(sorted((str(k).lower(), _normalize(v)) for k, v in (headers or {}).items()))
    return (url, items, header_items)


def _cache_get(key: tuple) -> requests.Response | None:
    if not CACHE_ENABLED:
        return None
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        expires_at, resp = entry
        if time.monotonic() >= expires_at:
            del _cache[key]
            return None
        _cache.move_to_end(key)
        return resp


def _cache_set(key: tuple, resp: requests.Response, *, ttl: float | None = None) -> None:
    if not CACHE_ENABLED:
        return
    effective_ttl = CACHE_TTL_SECONDS if ttl is None else ttl
    with _cache_lock:
        _cache[key] = (time.monotonic() + effective_ttl, resp)
        _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)


def clear_cache() -> None:
    """پاک‌کردنِ دستیِ کشِ GET."""
    with _cache_lock:
        _cache.clear()


def _note_network_response() -> None:
    """ثبتِ اینکه یک درخواستِ واقعی به کدال رفت (برای محاسبه‌ی فاصله‌ی بین نمادها)."""
    global _last_network_time
    _last_network_time = time.monotonic()


def symbol_pause(symbol: str) -> float:
    """اگر نماد عوض شده باشد، تا گذشتنِ ۱۵ تا ۲۰ ثانیه از آخرین درخواستِ واقعیِ نمادِ قبلی صبر می‌کند.

    * اولین نماد، و ادامه‌ی همان نماد (چند جست‌وجو/چند صفحه)، هیچ صبری ندارد.
    * اگر نمادِ قبلی کاملاً از کش/انبار آمده و به شبکه نرفته باشد، لازم نیست صبر کند.
    * مدتی که قبلاً سپری شده از صبر کم می‌شود (مثلاً پردازشِ طولانیِ نمادِ قبلی).
    مقدارِ ثانیه‌های صبرِ واقعی را برمی‌گرداند.
    """
    global _current_symbol
    key = str(symbol).strip()
    with _symbol_lock:
        if key == _current_symbol:
            return 0.0
        previous, _current_symbol = _current_symbol, key
        if previous is None or _last_network_time <= 0 or SYMBOL_MAX_DELAY <= 0:
            return 0.0
        gap = random.uniform(SYMBOL_MIN_DELAY, SYMBOL_MAX_DELAY)
        wait = gap - (time.monotonic() - _last_network_time)
    if wait > 0:
        logger.info("فاصله‌ی بین نمادها: %.1f ثانیه صبر قبل از %s", wait, key)
        time.sleep(wait)
        return wait
    return 0.0


def reset_symbol_pacing() -> None:
    """فراموش‌کردنِ نمادِ جاری (برای تست یا شروعِ یک دسته‌ی تازه)."""
    global _current_symbol, _last_network_time
    with _symbol_lock:
        _current_symbol = None
        _last_network_time = 0.0


def configure(
    *,
    min_delay: float | None = None,
    max_delay: float | None = None,
    rate_limit_sleep: float | None = None,
    max_retries: int | None = None,
    circuit_cooldown_seconds: float | None = None,
    cache_enabled: bool | None = None,
    cache_ttl_seconds: float | None = None,
    cache_max_entries: int | None = None,
    log_level: int | str | None = None,
    user_agent: str | None = None,
    rate_limit_min_interval: float | None = None,
    rate_limit_penalty_seconds: float | None = None,
    symbol_min_delay: float | None = None,
    symbol_max_delay: float | None = None,
) -> None:
    """تنظیمِ لایه‌ی HTTP؛ پارامترهایی که داده نشوند دست‌نخورده می‌مانند."""
    global MIN_DELAY, MAX_DELAY, RATE_LIMIT_SLEEP, MAX_RETRIES, CIRCUIT_COOLDOWN_SECONDS
    global SYMBOL_MIN_DELAY, SYMBOL_MAX_DELAY
    global CACHE_ENABLED, CACHE_TTL_SECONDS, CACHE_MAX_ENTRIES
    global RATE_LIMIT_MIN_INTERVAL, RATE_LIMIT_PENALTY_SECONDS
    new_min_delay = MIN_DELAY if min_delay is None else float(min_delay)
    new_max_delay = MAX_DELAY if max_delay is None else float(max_delay)
    if new_min_delay < 0 or new_max_delay < new_min_delay:
        raise ValueError("min_delay و max_delay باید نامنفی باشند و max_delay >= min_delay باشد")
    new_sym_min = SYMBOL_MIN_DELAY if symbol_min_delay is None else float(symbol_min_delay)
    new_sym_max = SYMBOL_MAX_DELAY if symbol_max_delay is None else float(symbol_max_delay)
    if new_sym_min < 0 or new_sym_max < new_sym_min:
        raise ValueError("symbol_min_delay و symbol_max_delay باید نامنفی باشند و symbol_max_delay >= symbol_min_delay باشد")
    if symbol_min_delay is not None:
        SYMBOL_MIN_DELAY = new_sym_min
    if symbol_max_delay is not None:
        SYMBOL_MAX_DELAY = new_sym_max
    if min_delay is not None:
        MIN_DELAY = new_min_delay
    if max_delay is not None:
        MAX_DELAY = new_max_delay
    if rate_limit_sleep is not None:
        RATE_LIMIT_SLEEP = rate_limit_sleep
    if max_retries is not None:
        MAX_RETRIES = max_retries
    if circuit_cooldown_seconds is not None:
        CIRCUIT_COOLDOWN_SECONDS = circuit_cooldown_seconds
    if cache_enabled is not None:
        CACHE_ENABLED = cache_enabled
    if cache_ttl_seconds is not None:
        CACHE_TTL_SECONDS = cache_ttl_seconds
    if cache_max_entries is not None:
        CACHE_MAX_ENTRIES = cache_max_entries
    if rate_limit_min_interval is not None:
        RATE_LIMIT_MIN_INTERVAL = rate_limit_min_interval
    if rate_limit_penalty_seconds is not None:
        RATE_LIMIT_PENALTY_SECONDS = rate_limit_penalty_seconds
    if log_level is not None:
        logger.setLevel(log_level)
    if user_agent is not None:
        HEADERS["User-Agent"] = user_agent
        _session.headers.update({"User-Agent": user_agent})


def safe_mode() -> None:
    """تنظیماتِ محافظه‌کارانه برای رباتِ بلندمدت."""
    configure(min_delay=2.0, max_delay=3.5, rate_limit_sleep=5, max_retries=2,
              rate_limit_min_interval=5.0, rate_limit_penalty_seconds=180,
              circuit_cooldown_seconds=30,
              symbol_min_delay=15.0, symbol_max_delay=20.0)


def fast_mode() -> None:
    """پروفایلِ سریع‌تر و همچنان محدود برای اجرای تعاملیِ سبک (فقط تأخیر و retry را کم می‌کند)."""
    configure(min_delay=0.03, max_delay=0.10, rate_limit_sleep=2, max_retries=2, circuit_cooldown_seconds=5,
              symbol_min_delay=0.0, symbol_max_delay=0.0)


def _wait_out_circuit_breaker() -> None:
    remaining = _circuit_blocked_until - time.monotonic()
    if remaining > 0:
        logger.warning(
            "مدارِ قطع‌کننده فعاله (احتمالِ بلاکِ موقتِ سرور) -- %s صبر می‌کنیم قبلِ از ادامه.",
            _format_duration(remaining),
        )
        time.sleep(remaining)


def _describe_cause(exc: Exception | None, status_code: int | None) -> str:
    """خلاصه‌ی خوانا از آخرین خطای یک درخواست، برای عیب‌یابیِ شبکه."""
    if exc is not None:
        return f"{type(exc).__name__}: {exc}"
    if status_code is not None:
        return f"HTTP {status_code}"
    return "نامشخص"


def _open_circuit_breaker() -> None:
    global _circuit_blocked_until
    with _circuit_lock:
        _circuit_blocked_until = time.monotonic() + CIRCUIT_COOLDOWN_SECONDS
    logger.warning(
        "یک درخواست پس از %s تلاش کامل شکست خورد -- مدارِ قطع‌کننده به‌مدتِ %s باز شد "
        "(درخواست‌های بعدی اول این مدت را صبر می‌کنند).",
        MAX_RETRIES, _format_duration(CIRCUIT_COOLDOWN_SECONDS),
    )


def reset_circuit_breaker() -> None:
    """پاک‌کردنِ دستیِ کول‌داونِ مدارِ قطع‌کننده (برای تست یا وقتی مطمئنی بلاک برطرف شده)."""
    global _circuit_blocked_until, _rate_limited_until
    with _circuit_lock:
        _circuit_blocked_until = 0.0
        _rate_limited_until = 0.0


def _looks_like_block_page(resp: Any) -> bool:
    """پاسخ‌های «موفق» که در واقع صفحه‌ی بلاک‌اند را تشخیص می‌دهد (برای requests و httpx)."""
    try:
        content = resp.content
        preview = content[:4096].decode("utf-8", errors="ignore")
    except Exception:
        return False
    # صفحه‌ی بلاکِ WAF کوچک و HTML است. پاسخِ JSON (جست‌وجو) یا صفحه‌ی بزرگِ گزارش ممکن است عبارتی مثل
    # «مسدود شده» را در متنِ یک اطلاعیه داشته باشد و نباید به‌عنوانِ بلاک ردّ و دوباره درخواست شود.
    if len(content) > _BLOCK_PAGE_MAX_BYTES or preview.lstrip().startswith(("{", "[")):
        return False
    return any(marker in preview for marker in _BLOCK_MARKERS)


def _retry_after_seconds(resp: Any) -> float | None:
    """اگر سرور Retry-After داده باشد (ثانیه یا تاریخِ HTTP)، همان را برمی‌گرداند."""
    value = resp.headers.get("Retry-After")
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        from datetime import datetime, timezone
        from email.utils import parsedate_to_datetime

        target = parsedate_to_datetime(value)
        if target.tzinfo is None:  # تاریخِ بدون منطقه‌ی زمانی (خلافِ استاندارد) را UTC فرض می‌کنیم
            target = target.replace(tzinfo=timezone.utc)
        return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None




def _reserve_request_delay() -> float:
    """رزرو فاصله‌ی بعدی به‌صورت thread-safe برای همه‌ی مسیرهای sync/async."""
    global _last_request_time
    with _throttle_lock:
        now = time.monotonic()
        delay = random.uniform(MIN_DELAY, MAX_DELAY)
        if now < _rate_limited_until:
            delay = max(delay, RATE_LIMIT_MIN_INTERVAL)
        scheduled = max(now, _last_request_time + delay)
        _last_request_time = scheduled
        return max(0.0, scheduled - now)


def _polite_sleep() -> None:
    """فاصله‌ی مؤدبانه‌ی رزروشده؛ فراخوانی‌های هم‌زمان پشت‌سرهم صف می‌شوند."""
    wait = _reserve_request_delay()
    if wait:
        time.sleep(wait)


def get(
    url: str,
    *,
    params: dict | None = None,
    timeout: float = 30.0,
    headers: dict | None = None,
    cache_ttl: float | None = None,
    use_cache: bool = True,
) -> requests.Response:
    """یک GET با فاصله‌ی مؤدبانه، retry برای 429/5xx/بلاک و خطای شبکه.

    * پاسخِ کش‌شده قبل از هر کاری (حتی وقتی مدار باز است) برگردانده می‌شود.
    * خطای دائمی (مثلاً 404) فوراً NetworkError می‌دهد؛ بدون retry و بدون باز کردنِ مدار.
    * بعد از MAX_RETRIES تلاشِ ناموفق، مدار باز می‌شود و NetworkError می‌آید.
    """
    effective_headers = {**HEADERS, **(headers or {})}
    key = _cache_key(url, params, effective_headers)
    if use_cache:
        cached = _cache_get(key)
        if cached is not None:
            logger.debug("کش: %s", url)
            return cached

    _wait_out_circuit_breaker()

    last_exc: Exception | None = None
    last_status_code: int | None = None
    total_delay = 0.0

    def _bounded_sleep(seconds: float) -> None:
        nonlocal total_delay
        seconds = min(seconds, max(0.0, MAX_TOTAL_RETRY_DELAY_SECONDS - total_delay))
        if seconds > 0:
            time.sleep(seconds)
        total_delay += seconds

    rate_limited_attempts = 0
    retry_after_hint: float | None = None
    attempts = max(1, int(MAX_RETRIES))

    for attempt in range(1, attempts + 1):
        is_last = attempt >= attempts
        _polite_sleep()
        try:
            resp = _session.get(url, params=params, timeout=timeout, headers=headers)
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            sleep_for = _full_jitter_backoff(1.0, attempt, 8.0)
            is_reset = "reset" in str(exc).lower() or "aborted" in str(exc).lower()
            hint = " (شبیهِ بلاکِ موقتِ سرور؛ اگر چند بار پشت‌سرهم دیدیش، بهتر است چند ساعت صبر کنی)" if is_reset else ""
            if is_last:
                logger.warning("خطای شبکه (تلاش %s/%s): %s%s", attempt, attempts, exc, hint)
                break
            logger.warning("خطای شبکه (تلاش %s/%s): %s%s -- %.0fs صبر", attempt, attempts, exc, hint, sleep_for)
            _bounded_sleep(sleep_for)
            continue

        last_status_code = resp.status_code
        _note_network_response()

        if resp.status_code == 200 and _looks_like_block_page(resp):
            if is_last:
                logger.warning("صفحه‌ی بلاک با status=200 (تلاش %s/%s)", attempt, attempts)
                break
            sleep_for = _full_jitter_backoff(1.0, attempt, 8.0)
            logger.warning("صفحه‌ی بلاک با status=200 (تلاش %s/%s) -- %.0fs صبر", attempt, attempts, sleep_for)
            _bounded_sleep(sleep_for)
            continue

        if resp.status_code == 429:
            rate_limited_attempts += 1
            _note_rate_limited()
            retry_after_hint = _retry_after_seconds(resp)
            if is_last:
                logger.warning("429 نرخ‌محدودیت (تلاش %s/%s)", attempt, attempts)
                break
            remaining_budget = max(0.0, MAX_TOTAL_RETRY_DELAY_SECONDS - total_delay)
            if retry_after_hint is not None and retry_after_hint > remaining_budget:
                # محدودیت سرور را نقض نکن: retry زودتر از Retry-After بدتر از توقف و گزارش خطاست.
                logger.warning("Retry-After از بودجه‌ی انتظار بیشتر است؛ retry متوقف شد")
                break
            sleep_for = (
                retry_after_hint
                if retry_after_hint is not None
                else _rate_limit_backoff(RATE_LIMIT_SLEEP, attempt, RATE_LIMIT_SLEEP * 4)
            )
            logger.warning("429 نرخ‌محدودیت (تلاش %s/%s) -- %.0fs صبر", attempt, attempts, sleep_for)
            _bounded_sleep(sleep_for)
            continue

        if resp.status_code in RETRYABLE_STATUS_CODES:
            if is_last:
                logger.warning("خطای سرور %s (تلاش %s/%s)", resp.status_code, attempt, attempts)
                break
            sleep_for = _full_jitter_backoff(1.0, attempt, 8.0)
            logger.warning("خطای سرور %s (تلاش %s/%s) -- %.0fs صبر", resp.status_code, attempt, attempts, sleep_for)
            _bounded_sleep(sleep_for)
            continue

        if resp.status_code >= 400:
            # خطای دائمی (مثلاً 404 برای اطلاعیه‌ی حذف‌شده). به‌جای HTTPErrorِ خامِ requests،
            # NetworkError می‌دهیم تا حلقه‌های بالادستی (که NetworkError را می‌گیرند) ادامه دهند.
            raise NetworkError(f"HTTP {resp.status_code} برای {url}", url=url, status_code=resp.status_code)

        logger.info("HTTP GET %s status=%s attempt=%s", url, resp.status_code, attempt)
        if use_cache:
            _cache_set(key, resp, ttl=cache_ttl)
        return resp

    _open_circuit_breaker()
    if last_status_code == 429 and rate_limited_attempts:
        wait_hint = (
            f"سرور گفته {_format_duration(retry_after_hint)} صبر کن"
            if retry_after_hint
            else f"حدود {_format_duration(RATE_LIMIT_PENALTY_SECONDS)} صبر کن و دوباره اجرا کن"
        )
        raise RateLimitError(
            f"درخواست پس از {attempts} تلاش شکست خورد: {url} -- آخرین خطا: HTTP 429 "
            f"(کدال تعدادِ درخواست‌ها را محدود کرده؛ {wait_hint})",
            url=url,
            retry_after=retry_after_hint,
        ) from last_exc
    raise NetworkError(
        f"درخواست پس از {attempts} تلاش شکست خورد: {url} -- آخرین خطا: "
        f"{_describe_cause(last_exc, last_status_code)}",
        url=url,
        status_code=last_status_code,
    ) from last_exc


# ---------------------------------------------------------------------------
# مسیر 1.0: کلاینتِ پایدارِ httpx (همان سیاستِ get() را دارد)
# ---------------------------------------------------------------------------

def _classify_modern(response: Any) -> str:
    """نتیجه‌ی یک پاسخِ httpx: 'ok' | 'retry' | 'fail'."""
    status = response.status_code
    if status == 200 and _looks_like_block_page(response):
        return "retry"
    if status == 429 or status in RETRYABLE_STATUS_CODES:
        return "retry"
    if status >= 400:
        return "fail"
    return "ok"


class CodalHTTPClient:
    """کلاینتِ پایدار و sync/async با اتصال‌های pool شده و همان سیاستِ مؤدبانه و retry."""

    def __init__(self, config: CodalConfig | None = None, *, cache: Cache | None = None) -> None:
        if httpx is None:
            raise ImportError(
                "CodalHTTPClient نیاز به بسته‌ی httpx دارد که نصب نیست. با `pip install httpx` نصبش کن، "
                "یا توابع اصلی (income_statement/balance_sheet/search_letters) را بدون client صدا بزن."
            )
        self.config = config or DEFAULT_CONFIG
        self.cache = cache if cache is not None else Cache(
            self.config.cache_dir, ttl=self.config.cache_ttl, enabled=not self.config.no_cache
        )
        self._client = httpx.Client(
            timeout=self.config.timeout,
            headers=self._headers(),
            follow_redirects=True,
            limits=self._limits(),
        )
        self._async_client: Any = None
        self.logger = logging.getLogger("alilnvest_codal.httpx")
        self._closed = False
        self._circuit_blocked_until = 0.0
        self._circuit_lock = threading.Lock()

    def _headers(self) -> dict[str, str]:
        return {**BROWSER_HEADERS, "User-Agent": self.config.user_agent}

    @staticmethod
    def _limits() -> Any:
        return httpx.Limits(max_connections=20, max_keepalive_connections=10)

    def _backoff(self, attempt: int) -> float:
        return self.config.delay * (2 ** (attempt - 1)) + random.random() * self.config.delay

    def _retry_pause(self, attempt: int, status: int | None) -> float:
        """429 با همان بک‌آفِ مسیرِ requests (حداقل نصفِ سقف)؛ قبلاً ~۱ تا ۲ ثانیه بود و دوباره 429 می‌گرفت."""
        if status == 429:
            return _rate_limit_backoff(RATE_LIMIT_SLEEP, attempt, RATE_LIMIT_SLEEP * 4)
        return self._backoff(attempt)

    def __enter__(self) -> "CodalHTTPClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._client.close()
            self._closed = True

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("CodalHTTPClient بسته شده است")
        remaining = self._circuit_blocked_until - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

    def _open_instance_circuit(self) -> None:
        with self._circuit_lock:
            self._circuit_blocked_until = time.monotonic() + max(0.0, CIRCUIT_COOLDOWN_SECONDS)

    def _request_key(self, url: str, params: dict[str, Any] | None) -> str:
        return self.cache.key(url, sorted((params or {}).items()), sorted(self._headers().items()))

    def get(self, url: str, *, params: dict[str, Any] | None = None, use_cache: bool = True) -> Any:
        self._check_open()
        key = self._request_key(url, params)
        if use_cache and (cached := self.cache.get(key)) is not None:
            return httpx.Response(200, content=cached, request=httpx.Request("GET", url))
        attempts = max(1, int(self.config.max_retries))
        last_exc: Exception | None = None
        last_status: int | None = None
        retry_after_hint: float | None = None
        for attempt in range(1, attempts + 1):
            _polite_sleep()
            started = perf_counter()
            hint: float | None = None
            try:
                response = self._client.get(url, params=params)
            except httpx.RequestError as exc:
                last_exc = exc
            else:
                last_status = response.status_code
                _note_network_response()
                if response.status_code == 404:
                    raise CodalNotFoundError(f"Codal resource was not found: {url}", url=url)
                verdict = _classify_modern(response)
                if verdict == "ok":
                    with self._circuit_lock:
                        self._circuit_blocked_until = 0.0
                    if use_cache:
                        self.cache.set(key, response.content)
                    self.logger.info("GET %s status=%s duration=%.3fs", url, response.status_code, perf_counter() - started)
                    return response
                if verdict == "fail":
                    raise NetworkError(f"HTTP {response.status_code} برای {url}", url=url, status_code=response.status_code)
                if response.status_code == 429:
                    _note_rate_limited()
                    hint = _retry_after_seconds(response)
                    retry_after_hint = hint
                last_exc = httpx.HTTPStatusError(
                    f"HTTP {response.status_code}", request=response.request, response=response
                )
            if attempt < attempts:
                if hint is not None and hint > MAX_TOTAL_RETRY_DELAY_SECONDS:
                    break
                time.sleep(hint if hint is not None else self._retry_pause(attempt, last_status))
        if last_status == 429:
            self._open_instance_circuit()
            raise RateLimitError(
                f"درخواست پس از {attempts} تلاش با HTTP 429 متوقف شد؛ مدتی صبر کنید و سپس دوباره امتحان کنید: {url}",
                url=url,
                retry_after=retry_after_hint,
            ) from last_exc
        self._open_instance_circuit()
        raise NetworkError(
            f"GET failed after {attempts} attempts: {url} -- last error: {_describe_cause(last_exc, last_status)}", url=url, status_code=last_status
        ) from last_exc

    async def _ensure_async(self) -> Any:
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(
                timeout=self.config.timeout,
                headers=self._headers(),
                follow_redirects=True,
                limits=self._limits(),
            )
        return self._async_client

    async def aget(self, url: str, *, params: dict[str, Any] | None = None, use_cache: bool = True) -> Any:
        self._check_open()
        key = self._request_key(url, params)
        if use_cache and (cached := self.cache.get(key)) is not None:
            return httpx.Response(200, content=cached, request=httpx.Request("GET", url))
        client = await self._ensure_async()
        attempts = max(1, int(self.config.max_retries))
        last_exc: Exception | None = None
        last_status: int | None = None
        retry_after_hint: float | None = None
        for attempt in range(1, attempts + 1):
            hint: float | None = None
            await asyncio.sleep(_reserve_request_delay())
            try:
                response = await client.get(url, params=params)
            except httpx.RequestError as exc:
                last_exc = exc
            else:
                last_status = response.status_code
                _note_network_response()
                if response.status_code == 404:
                    raise CodalNotFoundError(f"Codal resource was not found: {url}", url=url)
                verdict = _classify_modern(response)
                if verdict == "ok":
                    with self._circuit_lock:
                        self._circuit_blocked_until = 0.0
                    if use_cache:
                        self.cache.set(key, response.content)
                    return response
                if verdict == "fail":
                    raise NetworkError(f"HTTP {response.status_code} برای {url}", url=url, status_code=response.status_code)
                if response.status_code == 429:
                    _note_rate_limited()
                    hint = _retry_after_seconds(response)
                    retry_after_hint = hint
                last_exc = httpx.HTTPStatusError(
                    f"HTTP {response.status_code}", request=response.request, response=response
                )
            if attempt < attempts:
                if hint is not None and hint > MAX_TOTAL_RETRY_DELAY_SECONDS:
                    break
                await asyncio.sleep(hint if hint is not None else self._retry_pause(attempt, last_status))
        if last_status == 429:
            self._open_instance_circuit()
            raise RateLimitError(
                f"درخواست پس از {attempts} تلاش با HTTP 429 متوقف شد؛ مدتی صبر کنید و سپس دوباره امتحان کنید: {url}",
                url=url,
                retry_after=retry_after_hint,
            ) from last_exc
        self._open_instance_circuit()
        raise NetworkError(
            f"GET failed after {attempts} attempts: {url} -- last error: {_describe_cause(last_exc, last_status)}", url=url, status_code=last_status
        ) from last_exc

    async def aclose(self) -> None:
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None
        self.close()

    async def __aenter__(self) -> "CodalHTTPClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()
