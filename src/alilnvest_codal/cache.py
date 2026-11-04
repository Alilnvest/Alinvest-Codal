"""TTL cache with bounded memory and optional safe on-disk persistence."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import struct
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

_MAGIC = b"CPC1"
_HEADER = struct.Struct("!4sdc")
_KIND_BYTES = b"b"
_KIND_JSON = b"j"
_SUFFIX = ".cache"


def _encode(value: Any) -> tuple[bytes, bytes]:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return _KIND_BYTES, bytes(value)
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return _KIND_JSON, text.encode("utf-8")


class Cache:
    """TTL cache با لایه‌ی حافظه (LRU) و لایه‌ی اختیاری دیسک.

    مقادیر mutable هنگام ورود و خروج کپی می‌شوند تا تغییر مصرف‌کننده، cache را آلوده نکند.
    """

    def __init__(self, directory: str | os.PathLike[str] | None = None, ttl: float = 3600.0,
                 enabled: bool = True, *, max_memory_entries: int = 512) -> None:
        self.directory = Path(directory).expanduser() if directory else None
        self.ttl = self._ttl(ttl)
        self.enabled = bool(enabled)
        self.max_memory_entries = max(1, int(max_memory_entries))
        self._memory: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.RLock()
        if self.directory and self.enabled:
            self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _ttl(value: float) -> float:
        value = float(value)
        if not math.isfinite(value) or value < 0:
            raise ValueError("ttl must be a finite non-negative number")
        return value

    @staticmethod
    def key(*parts: object) -> str:
        return hashlib.sha256(repr(tuple(parts)).encode("utf-8")).hexdigest()

    def get(self, key: str) -> Any | None:
        if not self.enabled:
            return None
        now = time.time()
        with self._lock:
            entry = self._memory.get(key)
            if entry is not None:
                if entry[0] > now:
                    self._memory.move_to_end(key)
                    return copy.deepcopy(entry[1])
                del self._memory[key]
            if self.directory is None:
                return None
            loaded = self._read_file(key, now)
            if loaded is None:
                return None
            self._memory[key] = loaded
            self._evict(now)
            return copy.deepcopy(loaded[1])

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        if not self.enabled:
            return
        duration = self.ttl if ttl is None else self._ttl(ttl)
        expires = time.time() + duration
        stored = copy.deepcopy(value)
        encoded = _encode(stored) if self.directory is not None else None
        # Persist first. If it fails, the old in-memory value remains intact.
        with self._lock:
            if encoded is not None:
                self._write_file(key, expires, *encoded)
            self._memory[key] = (expires, stored)
            self._memory.move_to_end(key)
            self._evict(time.time())

    def clear(self) -> None:
        with self._lock:
            self._memory.clear()
            if self.directory and self.directory.exists():
                for pattern in (f"*{_SUFFIX}", "*.pickle"):
                    for path in self.directory.glob(pattern):
                        try:
                            path.unlink(missing_ok=True)
                        except OSError:
                            pass

    def _evict(self, now: float) -> None:
        for stale in [k for k, (exp, _) in self._memory.items() if exp <= now]:
            del self._memory[stale]
        while len(self._memory) > self.max_memory_entries:
            self._memory.popitem(last=False)

    def _path(self, key: str) -> Path:
        assert self.directory is not None
        return self.directory / f"{key}{_SUFFIX}"

    def _write_file(self, key: str, expires: float, kind: bytes, blob: bytes) -> None:
        path = self._path(key)
        fd, tmp = tempfile.mkstemp(prefix=f".{key[:16]}.", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(_HEADER.pack(_MAGIC, expires, kind))
                stream.write(blob)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _read_file(self, key: str, now: float) -> tuple[float, Any] | None:
        path = self._path(key)
        try:
            raw = path.read_bytes()
        except OSError:
            return None
        try:
            magic, expires, kind = _HEADER.unpack_from(raw)
            if magic != _MAGIC or not math.isfinite(expires):
                raise ValueError("bad cache header")
            blob = raw[_HEADER.size:]
            if kind == _KIND_BYTES:
                value: Any = blob
            elif kind == _KIND_JSON:
                value = json.loads(blob.decode("utf-8"))
            else:
                raise ValueError("unknown cache kind")
        except (struct.error, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        if expires <= now:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        return expires, value
