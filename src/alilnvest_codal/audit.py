"""ممیزیِ چندنمادی: برای هر نماد آخرین صورت‌های مالی را می‌گیرد، بررسی‌های checks را اجرا می‌کند،
و خروجیِ ساخت‌یافته (فهرست بررسی‌ها، جمع‌بندی، CSV و JSON، و داده‌ی خامِ هر صورت) می‌دهد.

این ماژول به شبکه وابسته است؛ بررسی‌های خالص در checks.py هستند و جدا آزموده می‌شوند.
خطای یک نماد یا یک بخش تحلیل، بقیه‌ی نمادها و بخش‌ها را متوقف نمی‌کند.
صورت‌های میان‌دوره با هم سنجیده می‌شوند؛ صورت‌های سالانه جدا گزارش می‌شوند، چون از اطلاعیه‌ی دیگری‌اند.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .checks import CheckResult, analyze_portfolio, analyze_set, analyze_statement, fa_digits, row_key, summarize
from .amendments import compare_with_original, find_original, is_amendment
from .exceptions import CodalError, NetworkError
from .utils import jalali_month_days
from .reports import (balance_sheet, cash_flow_statement, comprehensive_income_statement,
                      equity_changes_statement, fund_portfolio, income_statement)

__all__ = ["KIND_CHOICES", "SymbolAudit", "audit_symbol", "audit_symbols", "detect_kind",
           "prior_window", "write_reports", "write_raw"]

log = logging.getLogger("alilnvest_codal.audit")
KIND_CHOICES = ("auto", "company", "bank", "fund")
KIND_NAMES = {"balance": "ترازنامه", "income": "سود و زیان", "cash_flow": "جریان نقد",
              "equity": "صورت تغییرات حقوق مالکانه", "comprehensive": "صورت سود و زیان جامع"}
# از 1.4.3 شناسه‌ی غیرتلفیقی (1060/1058) و از 1.5.0 شناسه‌ی تلفیقی (1099/1097) هر دو تأییدشده‌اند؛
# این نام فقط برای سازگاری مانده و دیگر یادداشتِ «تأیید نشده» نمی‌سازد.
_UNCONFIRMED_STANDALONE: tuple[str, ...] = ()
_UNSAFE_FILE_CHARS = re.compile(r'[\\/:*?"<>|\s]+')


@dataclass
class SymbolAudit:
    symbol: str
    requested_kind: str = "auto"
    kind: str = "company"
    company_name: str = ""
    period_end: str = ""
    statements: dict[str, Any] = field(default_factory=dict)
    portfolio: Any = None
    checks: list[CheckResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    network_failed: bool = False

    def counts(self) -> dict[str, int]:
        return summarize(self.checks)

    def verdict(self) -> str:
        counts = self.counts()
        if self.errors or counts["error"]:
            return "error"
        if counts["warn"]:
            return "warn"
        if counts["ok"]:
            return "ok"
        return "info"

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "requested_kind": self.requested_kind, "kind": self.kind,
            "company_name": self.company_name, "period_end": self.period_end,
            "verdict": self.verdict(), "counts": self.counts(),
            "statements": sorted(self.statements), "notes": list(self.notes), "errors": list(self.errors),
            "network_failed": self.network_failed,
            "checks": [c.to_dict() for c in self.checks],
        }


def detect_kind(company_name: str, titles: Iterable[str] = ()) -> str:
    """صندوق اگر نام یا عنوان «صندوق» دارد، بانک اگر «بانک» دارد، و در غیر این صورت شرکت."""
    text = row_key(" ".join([company_name or "", *titles]))
    if "صندوق" in text:
        return "fund"
    if "بانک" in text:
        return "bank"
    return "company"


_jalali_month_days = jalali_month_days  # نام قدیمی برای سازگاری


def prior_window(period_end: str) -> tuple[str, str, str] | None:
    """برای تاریخ پایانِ دوره: (پایانِ همان دوره‌ی سال قبل، ابتدای بازهٔ انتشار، انتهای بازهٔ انتشار).

    دوره‌های کدال معمولاً به آخرینِ روزِ ماه ختم می‌شوند؛ پس اگر روزِ دوره‌ی جاری آخرین روزِ ماهش بود،
    پایانِ دوره‌ی سال قبل هم «آخرین روزِ همان ماه در سال قبل» است (مثلاً ۱۴۰۴/۱۲/۲۹ -> ۱۴۰۳/۱۲/۳۰ چون ۱۴۰۳
    کبیسه است، و ۱۴۰۳/۱۲/۳۰ -> ۱۴۰۲/۱۲/۲۹). کپیِ ساده‌ی روز تاریخِ ناموجود یا یک روز جابه‌جا می‌ساخت.
    """
    if len(period_end) != 10 or period_end[4] != "/" or period_end[7] != "/":
        return None
    try:
        year, month, day = int(period_end[:4]), int(period_end[5:7]), int(period_end[8:10])
    except ValueError:
        return None
    if not 1 <= month <= 12 or not 1 <= day <= _jalali_month_days(year, month):
        return None
    prior_len = _jalali_month_days(year - 1, month)
    prior_day = prior_len if day >= _jalali_month_days(year, month) else min(day, prior_len)
    prior_end = f"{year - 1}/{month:02d}/{prior_day:02d}"
    start = f"{year - 1}/{month:02d}/01"
    end_month = month + 3
    end_year = year if end_month > 12 else year - 1
    end_month = end_month - 12 if end_month > 12 else end_month
    end = f"{end_year}/{end_month:02d}/{_jalali_month_days(end_year, end_month):02d}"
    return prior_end, start, end


def _period_months(report: Any) -> int | None:
    raw = report.raw if isinstance(report.raw, dict) else {}
    try:
        return int(raw.get("period")) if raw.get("period") is not None else None
    except (TypeError, ValueError):
        return None


def _first(fetch: Any, symbol: str, *, audit: SymbolAudit, label: str, progress: bool, **kwargs: Any) -> Any:
    try:
        reports = fetch(symbol, max_reports=1, progress=progress, **kwargs)
    except NetworkError as exc:
        audit.network_failed = True
        audit.notes.append(f"{label}: {type(exc).__name__}: {exc}")
        return None
    except CodalError as exc:
        audit.notes.append(f"{label}: {type(exc).__name__}: {exc}")
        return None
    except Exception as exc:  # یک صورت خراب نباید کل نماد را از بین ببرد
        log.warning("%s برای %s شکست خورد: %s", label, symbol, exc, exc_info=True)
        audit.errors.append(f"{label}: {type(exc).__name__}: {exc}")
        return None
    if not reports:
        skipped = kwargs.get("skipped") or []
        if not any(s.get("reason") == "network" for s in skipped):
            audit.notes.append(f"{label}: صورتی برای این نماد پیدا نشد")
        return None
    return reports[0]


def _guard(audit: SymbolAudit, section: str, run: Callable[[], Iterable[CheckResult]]) -> list[CheckResult]:
    """اگر یک بخش تحلیل خطا بدهد، فقط همان بخش یک خطا می‌گیرد؛ بقیه‌ی بررسی‌های نماد سالم می‌مانند."""
    try:
        return list(run())
    except Exception as exc:
        log.warning("تحلیل %s برای %s شکست خورد: %s", section, audit.symbol, exc, exc_info=True)
        return [CheckResult("error", section, "تحلیل", f"{type(exc).__name__}: {exc}")]


def _prior_original(symbol: str, current: Any, *, audit: SymbolAudit, progress: bool, extra: dict[str, Any]) -> Any:
    window = prior_window(str(current.period_end_to_date or ""))
    if window is None:
        audit.notes.append("تاریخ پایان دوره برای یافتن گزارش اصلیِ سال قبل معتبر نیست")
        return None
    prior_end, start, end = window
    try:
        reports = cash_flow_statement(symbol, from_date=start, to_date=end, max_reports=4,
                                      progress=progress, **extra)
    except Exception as exc:  # جست‌وجوی گزارش اصلی اختیاری است؛ شکستش نباید نماد را خراب کند
        if isinstance(exc, NetworkError):
            audit.network_failed = True
        audit.notes.append(f"گزارش اصلی سال قبل: {type(exc).__name__}: {exc}")
        return None
    for rep in reports or []:
        if rep.period_end_to_date == prior_end and bool(rep.is_consolidated) == bool(current.is_consolidated):
            audit.notes.append(f"گزارش اصلی سال قبل: TracingNo={rep.tracing_no} | دوره {fa_digits(prior_end)} "
                               f"| منتشر شده {rep.publish_date_time or '-'}")
            return rep
    audit.notes.append(f"گزارش اصلی دورهٔ {fa_digits(prior_end)} در بازهٔ {fa_digits(start)} تا "
                       f"{fa_digits(end)} پیدا نشد؛ مقایسهٔ تجدیدارائه انجام نشد")
    return None


def _amendment_checks(symbol: str, audit: SymbolAudit, *, progress: bool, extra: dict[str, Any]) -> list[CheckResult]:
    """برای هر صورتِ اصلاحیه، نسخه‌ی اولیه‌ی همان دوره را پیدا می‌کند و عددهای تغییرکرده را می‌آورد."""
    amended = next(r for r in audit.statements.values() if is_amendment(r))
    window_from = str(amended.period_end_to_date or "")
    window_to = str(amended.publish_date_time or "")[:10]
    if not window_from or not window_to:
        return [CheckResult("skip", "اصلاحیه", "اطلاعیه‌ی اولیه",
                            "تاریخ پایان دوره یا تاریخ انتشار اصلاحیه در داده نیست")]
    checks: list[CheckResult] = []
    fetchers = {"balance": balance_sheet, "income": income_statement, "cash_flow": cash_flow_statement}
    for key, fetch in fetchers.items():
        current = audit.statements.get(key)
        if current is None:
            continue
        label = f"{KIND_NAMES.get(key, key)}: اطلاعیه‌ی اولیه"
        if audit.network_failed:
            checks.append(CheckResult("skip", "اصلاحیه", label, "کدال در این اجرا پاسخ نداد"))
            continue
        skipped: list[dict[str, Any]] = []
        try:
            candidates = fetch(symbol, from_date=window_from, to_date=window_to, max_reports=6,
                               progress=progress, skipped=skipped, **extra)
        except NetworkError as exc:
            audit.network_failed = True
            checks.append(CheckResult("skip", "اصلاحیه", label, f"جست‌وجو قطع شد: {exc}"))
            continue
        except Exception as exc:  # جست‌وجوی اولیه اختیاری است
            checks.append(CheckResult("skip", "اصلاحیه", label, f"جست‌وجو شکست خورد: {type(exc).__name__}: {exc}"))
            continue
        if any(s.get("reason") == "network" for s in skipped):
            checks.append(CheckResult("skip", "اصلاحیه", label, "جست‌وجوی اطلاعیه‌ی اولیه به‌خاطر خطای شبکه کامل نشد"))
            continue
        original = find_original(current, candidates or [])
        if original is None:
            checks.append(CheckResult("info", "اصلاحیه", label,
                                      f"در بازه‌ی {fa_digits(window_from)} تا {fa_digits(window_to)} پیدا نشد"))
            continue
        checks.append(compare_with_original(key, current, original))
    return checks


def audit_symbol(symbol: str, *, kind: str = "auto", full: bool = False, prior_original: bool = True,
                 store: Any | None = None, progress: bool = False,
                 compare_amendment: bool = False) -> SymbolAudit:
    """آخرین صورت‌های مالیِ یک نماد را می‌گیرد و همه‌ی بررسی‌ها را اجرا می‌کند.

    kind: auto (از روی نام و عنوان)، company، bank، یا fund (صندوق: علاوه بر صورت‌ها، پورتفو).
    full: علاوه بر ترازنامه، سود و زیان، جریان نقد، تغییرات حقوق مالکانه و سود جامع را هم می‌گیرد.
    prior_original: گزارش اصلیِ همان دورهٔ سال قبل را می‌گیرد تا تجدیدارائه‌ی جریان نقد سنجیده شود.
    """
    if kind not in KIND_CHOICES:
        raise ValueError(f"نوع نامعتبر {kind!r}؛ یکی از {KIND_CHOICES} باشد")
    audit = SymbolAudit(symbol=symbol, requested_kind=kind)
    extra: dict[str, Any] = {"store": store} if store is not None else {}

    fetchers = [("balance", balance_sheet), ("income", income_statement), ("cash_flow", cash_flow_statement)]
    if full:
        fetchers += [("equity", equity_changes_statement), ("comprehensive", comprehensive_income_statement)]
    for key, fetch in fetchers:
        if audit.network_failed:
            audit.notes.append(f"{key}: اجرا نشد؛ کدال در این اجرا پاسخ نداد")
            continue
        skipped: list[dict[str, Any]] = []
        report = _first(fetch, symbol, audit=audit, label=key, progress=progress, skipped=skipped, **extra)
        network = [s for s in skipped if s.get("reason") == "network"]
        if network:
            # یک اطلاعیه‌ی جدیدتر به‌خاطر خطای شبکه بررسی نشد؛ هر نتیجه‌ی قدیمی‌تر قابل اعتماد نیست
            audit.network_failed = True
            numbers = ", ".join(str(s["tracing_no"]) for s in network)
            audit.notes.append(f"{key}: اطلاعیه‌ی جدیدتر (TracingNo={numbers}) به‌خاطر خطای شبکه بررسی نشد؛ "
                               "نتیجه‌ی قدیمی‌تر کنار گذاشته شد")
            continue
        if report is not None:
            audit.statements[key] = report
            newer = [s for s in skipped if s.get("reason") in ("no_datasource", "rejected")]
            if newer:
                audit.checks.append(CheckResult(
                    "warn", "کلی", f"{KIND_NAMES.get(key, key)}: از جدیدترین اطلاعیه نیست",
                    f"اطلاعیه‌ی جدیدتر بدون داده‌ی قابل‌خواندن بود و کنار گذاشته شد؛ "
                    f"این صورت از اطلاعیه‌ی قدیمی‌تر TracingNo={report.tracing_no} است. "
                    "اگر آن اطلاعیه باید داده داشته باشد، با --forget-absent دوباره امتحان کنید.",
                    tuple(f"TracingNo={s['tracing_no']} | دلیل: {s['reason']}" for s in newer)))

    interim = {k: r for k, r in audit.statements.items() if _period_months(r) != 12}
    annual = {k: r for k, r in audit.statements.items() if _period_months(r) == 12}
    if audit.statements:
        latest = max(audit.statements.values(), key=lambda r: str(r.period_end_to_date or ""))
        audit.company_name = latest.company_name or ""
        audit.period_end = latest.period_end_to_date or ""
    titles = [r.title or "" for r in audit.statements.values()]
    if any("اصلاحیه" in row_key(title) for title in titles):
        audit.notes.append("اطلاعیه‌ی جاری «اصلاحیه» است؛ یعنی نسخه‌ی اصلاح‌شده‌ی اطلاعیه‌ی اولیه‌ی همین دوره")

    if interim:
        tracings = sorted({str(r.tracing_no) for r in interim.values()})
        periods = sorted({str(r.period_end_to_date) for r in interim.values()})
        scopes = sorted({"تلفیقی" if r.is_consolidated else "غیرتلفیقی" for r in interim.values()})
        audit.notes.append("اطلاعیه‌ی جاری: TracingNo=" + ", ".join(tracings))
        if len(tracings) > 1 or len(periods) > 1 or len(scopes) > 1:
            audit.checks.append(CheckResult(
                "warn", "کلی", "یکسانی اطلاعیه و دوره",
                f"صورت‌های میان‌دوره از یک اطلاعیه، یک دوره و یک نوع نیستند: TracingNo={', '.join(tracings)}؛ "
                f"دوره‌ها={', '.join(periods)}؛ نوع={', '.join(scopes)}"))
    for key, rep in annual.items():
        scope = "تلفیقی" if rep.is_consolidated else "غیرتلفیقی"
        note = f"{KIND_NAMES.get(key, key)}: از اطلاعیه‌ی سالانه TracingNo={rep.tracing_no} ({scope}) گرفته شد"
        if interim:
            note += " -- در اطلاعیه‌ی جاری پیدا نشد"
            if key in _UNCONFIRMED_STANDALONE:
                note += "؛ شناسه‌ی نسخه‌ی غیرتلفیقی این صورت هنوز تأیید نشده است"
        audit.notes.append(note)
    if annual:
        described = [f"{KIND_NAMES.get(k, k)} (TracingNo={r.tracing_no}، "
                     f"{'تلفیقی' if r.is_consolidated else 'غیرتلفیقی'})" for k, r in annual.items()]
        audit.checks.append(CheckResult(
            "info", "کلی", "صورت‌های سالانه",
            "این صورت‌ها از اطلاعیه‌ی سالانه گرفته شدند و با صورت‌های میان‌دوره مقایسه نمی‌شوند: " + "؛ ".join(described)))

    if kind == "auto" and not audit.statements and not audit.network_failed:
        # بدونِ صورتِ استاندارد، پورتفو تنها راهِ تشخیصِ صندوق بودن است
        audit.portfolio = _first(fund_portfolio, symbol, audit=audit, label="portfolio", progress=progress)
        if audit.portfolio is not None:
            audit.company_name = audit.portfolio.company_name or ""
            audit.period_end = audit.portfolio.period_end_to_date or ""
            titles.append(audit.portfolio.title or "")
    audit.kind = kind if kind != "auto" else detect_kind(audit.company_name, titles)
    if audit.kind == "fund" and audit.portfolio is None and not audit.network_failed:
        audit.portfolio = _first(fund_portfolio, symbol, audit=audit, label="portfolio", progress=progress)
    if audit.kind != "fund" and audit.portfolio is not None:
        audit.notes.append("پورتفو بررسی نشد؛ این نماد صندوق نیست")
        audit.portfolio = None

    prior_rep = None
    if prior_original and "cash_flow" in audit.statements and not audit.network_failed:
        prior_rep = _prior_original(symbol, audit.statements["cash_flow"], audit=audit,
                                    progress=progress, extra=extra)

    for key, rep in audit.statements.items():
        audit.checks.extend(_guard(audit, f"صورت: {key}",
                                   lambda rep=rep, key=key: analyze_statement(rep, kind=key)))
    audit.checks.extend(_guard(audit, "هم‌خوانی بین صورت‌ها",
                               lambda: analyze_set(audit.statements, prior=prior_rep)))
    if audit.portfolio is not None:
        audit.checks.extend(_guard(audit, "صندوق: صورت پورتفو", lambda: analyze_portfolio(audit.portfolio)))
    if not audit.statements and audit.portfolio is None:
        audit.checks.append(CheckResult("error", "کلی", "صورت مالی",
                                        "هیچ صورت مالی یا پورتفویی برای این نماد پیدا نشد"))
    if compare_amendment and audit.statements and any(is_amendment(r) for r in audit.statements.values()):
        audit.checks.extend(_guard(audit, "اصلاحیه",
                                   lambda: _amendment_checks(symbol, audit, progress=progress, extra=extra)))
    return audit


def audit_symbols(symbols: Iterable[str], *, kinds: dict[str, str] | None = None, **kwargs: Any) -> list[SymbolAudit]:
    """همه‌ی نمادها را یکی‌یکی ممیزی می‌کند؛ خطای هر نماد فقط در خروجیِ همان نماد ثبت می‌شود.

    اگر کدال در یک نماد پاسخ نداد (خطای شبکه)، نمادهای بعدی درخواست نمی‌شوند: ادامه دادن فقط بلاک را طولانی‌تر می‌کند.
    """
    kinds = kinds or {}
    results: list[SymbolAudit] = []
    network_down = False
    for symbol in symbols:
        requested = kinds.get(symbol, "auto")
        if network_down:
            skipped = SymbolAudit(symbol=symbol, requested_kind=requested, network_failed=True)
            skipped.errors.append("اجرا نشد: کدال در این اجرا پاسخ نداد (نماد قبلی خطای شبکه داشت)")
            results.append(skipped)
            continue
        try:
            audit = audit_symbol(symbol, kind=requested, **kwargs)
        except Exception as exc:  # دفاعی: هیچ نمادی نباید دسته را متوقف کند
            audit = SymbolAudit(symbol=symbol, requested_kind=requested)
            audit.errors.append(f"{type(exc).__name__}: {exc}")
        results.append(audit)
        if audit.network_failed:
            network_down = True
    return results


_SUMMARY_FIELDS = ["symbol", "requested_kind", "kind", "company_name", "period_end", "verdict",
                   "ok", "warn", "error", "info", "skip", "statements", "errors"]
_CHECK_FIELDS = ["symbol", "status", "section", "name", "message", "details"]
_DETAIL_SEPARATOR = " ‖ "


def write_reports(audits: Iterable[SymbolAudit], out_dir: str | Path) -> dict[str, Path]:
    """summary.csv (یک سطر برای هر نماد)، checks.csv (همه‌ی بررسی‌ها)، و audit.json (کامل)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    audits = list(audits)
    summary_path, checks_path, json_path = out / "summary.csv", out / "checks.csv", out / "audit.json"
    with summary_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_SUMMARY_FIELDS)
        writer.writeheader()
        for a in audits:
            c = a.counts()
            writer.writerow({
                "symbol": a.symbol, "requested_kind": a.requested_kind, "kind": a.kind,
                "company_name": a.company_name, "period_end": a.period_end, "verdict": a.verdict(),
                "ok": c["ok"], "warn": c["warn"], "error": c["error"], "info": c["info"], "skip": c["skip"],
                "statements": " ".join(sorted(a.statements)), "errors": " | ".join(a.errors),
            })
    with checks_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CHECK_FIELDS)
        writer.writeheader()
        for a in audits:
            for check in a.checks:
                writer.writerow({"symbol": a.symbol, "status": check.status, "section": check.section,
                                 "name": check.name, "message": check.message,
                                 "details": _DETAIL_SEPARATOR.join(check.details)})
    json_path.write_text(json.dumps([a.to_dict() for a in audits], ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return {"summary": summary_path, "checks": checks_path, "json": json_path}


def _file_stem(symbol: str) -> str:
    return _UNSAFE_FILE_CHARS.sub("_", symbol).strip("_") or "symbol"


def write_raw(audits: Iterable[SymbolAudit], out_dir: str | Path) -> list[Path]:
    """داده‌ی خامِ هر صورت (JSON)، جدولِ هر صورت (CSV)، و جدول و داده‌ی خامِ پورتفوی صندوق را کنار خروجی می‌گذارد.

    با این فایل‌ها می‌شود همان بررسی را دوباره، بدونِ اینترنت، روی همان داده اجرا کرد.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for audit in audits:
        stem = _file_stem(audit.symbol)
        for key, rep in audit.statements.items():
            base = f"{stem}_{key}_{rep.tracing_no}"
            raw_path = out / f"{base}_raw.json"
            raw_path.write_text(json.dumps(rep.raw, ensure_ascii=False, indent=1, default=str, allow_nan=False), encoding="utf-8")
            table_path = out / f"{base}_table.csv"
            rep.row_code_df.to_csv(table_path, encoding="utf-8-sig")
            written += [raw_path, table_path]
        if audit.portfolio is not None:
            port = audit.portfolio
            base = f"{stem}_portfolio_{port.tracing_no}"
            raw_path = out / f"{base}_raw.json"
            raw_path.write_text(json.dumps(port.raw, ensure_ascii=False, indent=1, default=str, allow_nan=False), encoding="utf-8")
            grid_path = out / f"{base}_grid.csv"
            port.grid_df.to_csv(grid_path, index=False, encoding="utf-8-sig")
            written += [raw_path, grid_path]
    return written
