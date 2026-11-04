"""Runtime configuration for alilnvest_codal."""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_LEVELS = {"CRITICAL": logging.CRITICAL, "FATAL": logging.FATAL, "ERROR": logging.ERROR,
           "WARN": logging.WARNING, "WARNING": logging.WARNING, "INFO": logging.INFO,
           "DEBUG": logging.DEBUG, "NOTSET": logging.NOTSET}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        value = default if raw is None or not raw.strip() else float(raw)
        if not math.isfinite(value):
            raise ValueError
        return value
    except (TypeError, ValueError):
        logging.getLogger("alilnvest_codal.config").warning("Ignoring invalid %s=%r", name, raw)
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return default if raw is None or not raw.strip() else int(raw)
    except (TypeError, ValueError):
        logging.getLogger("alilnvest_codal.config").warning("Ignoring invalid %s=%r", name, raw)
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    logging.getLogger("alilnvest_codal.config").warning("Ignoring invalid %s=%r", name, raw)
    return default


def parse_log_level(raw: str | None) -> int:
    if raw is None or not raw.strip():
        return logging.WARNING
    value = raw.strip().upper()
    if value.isdigit() and 0 <= int(value) <= 100:
        return int(value)
    return _LEVELS.get(value, logging.WARNING)


@dataclass(frozen=True, slots=True)
class CodalConfig:
    timeout: float = 30.0
    max_retries: int = 3
    delay: float = 1.0
    cache_ttl: float = 3600.0
    cache_dir: str | None = None
    no_cache: bool = False
    max_concurrency: int = 5
    log_level: int = logging.WARNING
    user_agent: str = DEFAULT_USER_AGENT

    def __post_init__(self) -> None:
        for name in ("timeout", "delay", "cache_ttl"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0 or (name == "timeout" and value == 0):
                raise ValueError(f"{name} must be finite and non-negative (timeout must be > 0)")
        if int(self.max_retries) < 0:
            raise ValueError("max_retries must be non-negative")
        if int(self.max_concurrency) < 1:
            raise ValueError("max_concurrency must be positive")
        if not isinstance(self.user_agent, str) or not self.user_agent.strip():
            raise ValueError("user_agent must not be empty")
        if self.cache_dir is not None and not str(self.cache_dir).strip():
            raise ValueError("cache_dir must not be empty")

    @classmethod
    def from_env(cls) -> "CodalConfig":
        return cls(timeout=max(0.001, _env_float("CODAL_TIMEOUT", 30.0)),
                   max_retries=max(0, _env_int("CODAL_MAX_RETRIES", 3)),
                   delay=max(0.0, _env_float("CODAL_DELAY", 1.0)),
                   cache_ttl=max(0.0, _env_float("CODAL_CACHE_TTL", 3600.0)),
                   cache_dir=os.getenv("CODAL_CACHE_DIR") or None,
                   no_cache=_env_bool("CODAL_NO_CACHE"),
                   max_concurrency=max(1, _env_int("CODAL_MAX_CONCURRENCY", 5)),
                   log_level=parse_log_level(os.getenv("CODAL_LOG_LEVEL")),
                   user_agent=os.getenv("CODAL_USER_AGENT", DEFAULT_USER_AGENT).strip() or DEFAULT_USER_AGENT)


def configure_logging(config: CodalConfig | None = None) -> None:
    logging.getLogger("alilnvest_codal").setLevel((config or CodalConfig.from_env()).log_level)


DEFAULT_CONFIG = CodalConfig.from_env()
configure_logging(DEFAULT_CONFIG)
