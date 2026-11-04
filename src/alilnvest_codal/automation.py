"""Orchestration for Codal bots."""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import reports as report_api
from .search import search_letters

logger = logging.getLogger("alilnvest_codal.automation")
_STATEMENT_OPERATIONS = frozenset({"income_statement", "balance_sheet", "cash_flow_statement",
                                   "equity_changes_statement", "comprehensive_income_statement", "monthly_activity"})


@dataclass(frozen=True)
class OperationResult:
    name: str
    status: str
    elapsed_seconds: float
    value: Any = None
    error_type: str | None = None
    error: str | None = None

    def summary(self) -> dict[str, Any]:
        result: dict[str, Any] = {"name": self.name, "status": self.status, "elapsed_seconds": self.elapsed_seconds}
        if isinstance(self.value, list):
            result["count"] = len(self.value)
        if self.error_type:
            result["error_type"] = self.error_type
            result["error"] = self.error
        return result


def _safe_name(text: str) -> str:
    return re.sub(r"[^\w\-]+", "_", str(text), flags=re.UNICODE).strip("._") or "_"


def _unique_file(directory: Path, stem: str, suffix: str) -> Path:
    candidate = directory / f"{stem}{suffix}"
    if not candidate.exists():
        return candidate
    return directory / f"{stem}_{uuid.uuid4().hex[:10]}{suffix}"


class CodalRun:
    def __init__(self, symbol: str, *, from_date: str | None = None, to_date: str | None = None,
                 progress: bool = False, use_cache: bool = True, store: Any | None = None) -> None:
        self.symbol, self.from_date, self.to_date = symbol, from_date, to_date
        self.progress, self.use_cache, self.store = progress, use_cache, store
        self._letters: list[Any] | None = None

    def letters(self, **filters: Any) -> list[Any]:
        if not filters and self._letters is not None:
            return list(self._letters)
        result = list(search_letters(self.symbol, from_date=self.from_date, to_date=self.to_date,
                                     progress=self.progress, use_cache=self.use_cache,
                                     raise_if_empty=False, **filters))
        if not filters:
            self._letters = result
        return list(result)

    def call(self, name: str, function: Any, **kwargs: Any) -> OperationResult:
        started = time.perf_counter()
        try:
            options = {"from_date": self.from_date, "to_date": self.to_date,
                       "progress": self.progress, "use_cache": self.use_cache}
            options.update(kwargs)
            value = function(self.symbol, **{k: v for k, v in options.items() if v is not None})
            return OperationResult(name, "ok", round(time.perf_counter() - started, 3), value=value)
        except Exception as exc:
            logger.warning("operation %s for %s failed: %s", name, self.symbol, exc, exc_info=True)
            return OperationResult(name, "error", round(time.perf_counter() - started, 3),
                                   error_type=type(exc).__name__, error=str(exc))

    def full_company_analysis(self, *, max_reports: int = 5, include_monthly: bool = True,
                              include_ownership: bool = False) -> dict[str, OperationResult]:
        operations: dict[str, Any] = {"income_statement": report_api.income_statement,
            "balance_sheet": report_api.balance_sheet, "cash_flow_statement": report_api.cash_flow_statement,
            "equity_changes_statement": report_api.equity_changes_statement,
            "comprehensive_income_statement": report_api.comprehensive_income_statement}
        if include_monthly:
            operations["monthly_activity"] = report_api.monthly_activity
        if include_ownership:
            operations.update({"subsidiary_companies": report_api.subsidiary_companies,
                               "affiliated_companies": report_api.affiliated_companies,
                               "board_members": report_api.board_members})
        results: dict[str, OperationResult] = {}
        rate_limited = False
        for name, function in operations.items():
            if rate_limited:
                results[name] = OperationResult(name, "skipped", 0.0, error_type="RateLimitError",
                                                error="اجرا نشد: عملیات قبلی 429 گرفت")
                continue
            extra = {"max_reports": max_reports}
            if self.store is not None and name in _STATEMENT_OPERATIONS:
                extra["store"] = self.store
            results[name] = self.call(name, function, **extra)
            rate_limited = results[name].error_type == "RateLimitError"
        return results


def fetch_since(symbol: str, *, from_date: str | None = None, to_date: str | None = None,
                last_tracing_no: int | None = None, **filters: Any) -> list[Any]:
    letters = search_letters(symbol, from_date=from_date, to_date=to_date, progress=False,
                             raise_if_empty=False, **filters)
    items = list(letters)
    return items if last_tracing_no is None else [x for x in items if int(x.tracing_no or 0) > int(last_tracing_no)]


class CodalBot:
    def __init__(self, *, symbols: list[str] | None = None, funds: list[str] | None = None,
                 output_dir: str | Path = "codal_results", from_date: str | None = None, store: Any | None = None) -> None:
        self.symbols, self.funds = list(symbols or []), list(funds or [])
        self.output_dir, self.from_date, self.store = Path(output_dir).expanduser(), from_date, store

    def run_daily(self, *, max_reports: int = 5) -> dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("run_%Y%m%d_%H%M%S_%fZ")
        run_dir = self.output_dir / f"{stamp}_{uuid.uuid4().hex[:8]}"
        run_dir.mkdir()
        result: dict[str, Any] = {"started_at": datetime.now(timezone.utc).isoformat(), "companies": {}, "funds": {}}
        blocked = False
        skipped_note = {"status": "skipped", "error_type": "RateLimitError", "error": "اجرا نشد: کدال 429 داد"}
        used_stems: set[str] = set()
        for symbol in self.symbols:
            if blocked:
                result["companies"][symbol] = dict(skipped_note)
                continue
            operations = CodalRun(symbol, from_date=self.from_date, progress=False, store=self.store).full_company_analysis(max_reports=max_reports)
            blocked = any(op.error_type == "RateLimitError" for op in operations.values())
            result["companies"][symbol] = {name: op.summary() for name, op in operations.items()}
            for name, operation in operations.items():
                if operation.status == "ok" and isinstance(operation.value, list):
                    for index, report in enumerate(operation.value, 1):
                        frame = getattr(report, "row_code_df", None)
                        if frame is None:
                            frame = getattr(report, "grid_df", None)
                        if frame is not None:
                            stem = f"{_safe_name(symbol)}_{_safe_name(name)}_{index}"
                            while stem in used_stems:
                                stem += "_x"
                            used_stems.add(stem)
                            frame.to_csv(_unique_file(run_dir, stem, ".csv"), encoding="utf-8-sig")
        for fund in self.funds:
            if blocked:
                result["funds"][fund] = dict(skipped_note)
                continue
            started = time.perf_counter()
            try:
                reports = report_api.fund_portfolio(fund, from_date=self.from_date, max_reports=max_reports,
                    progress=False, checkpoint_path=_unique_file(run_dir, _safe_name(fund) + ".checkpoint", ".json"))
                result["funds"][fund] = {"status": "ok", "count": len(reports), "elapsed_seconds": round(time.perf_counter() - started, 3)}
                for index, report in enumerate(reports, 1):
                    report.grid_df.to_csv(_unique_file(run_dir, f"{_safe_name(fund)}_portfolio_{index}", ".csv"), encoding="utf-8-sig", index=False)
            except Exception as exc:
                blocked = type(exc).__name__ == "RateLimitError"
                logger.warning("fund %s failed: %s", fund, exc, exc_info=True)
                result["funds"][fund] = {"status": "error", "error_type": type(exc).__name__, "error": str(exc)}
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        summary = run_dir / "run_summary.json"
        tmp = summary.with_suffix(".tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        tmp.replace(summary)
        result["output_dir"] = str(run_dir)
        return result


__all__ = ["CodalRun", "CodalBot", "OperationResult", "fetch_since"]
