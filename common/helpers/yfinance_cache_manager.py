"""Helper utilities for cleaning up :mod:`yfinance_cache` manager locks.

:yfinance_cache uses ``multiprocessing.Manager`` locks that spawn named semaphores
tracked by :mod:`multiprocessing.resource_tracker`. If the manager is not shut down,
the semaphores stay registered and trigger the "leaked semaphore" warnings on macOS.
This module exposes a helper that registers an :mod:`atexit` hook to shut down the
manager and release those semaphores when the process exits.
"""

from __future__ import annotations

import atexit
import os
import time
from pathlib import Path
from typing import Final

import yfinance_cache.yfc_cache_manager as yfc_cache_manager
import yfinance_cache.yfc_dat as yfc_dat


DEFAULT_MAX_CACHE_BYTES: Final[int] = 2 * 1024 * 1024 * 1024  # 2 GiB ceiling
DEFAULT_MAX_FILE_AGE_DAYS: Final[int] = 30
_REGISTERED = False
_CONFIGURED = False


def register_yfinance_cache_manager_cleanup() -> None:
    """Register an :mod:`atexit` hook to shut down the yfinance_cache manager."""
    global _REGISTERED
    if _REGISTERED:
        return
    _REGISTERED = True
    atexit.register(_shutdown_manager)


def configure_yfinance_cache(
    cache_dir: str | Path | None = None,
    max_cache_bytes: int | None = None,
    max_file_age_days: int = DEFAULT_MAX_FILE_AGE_DAYS,
) -> Path:
    """Set cache location and prune stale Yahoo Finance cache files."""
    global _CONFIGURED
    target_dir = _resolve_cache_dir(cache_dir)
    _prune_yfinance_cache(target_dir, _resolve_size_limit(max_cache_bytes), _resolve_age_days(max_file_age_days))
    _CONFIGURED = True
    return target_dir


def init_yfinance_cache(
    cache_dir: str | Path | None = None,
    max_cache_bytes: int | None = None,
    max_file_age_days: int = DEFAULT_MAX_FILE_AGE_DAYS,
) -> Path:
    """Configure cache and register cleanup in one call."""
    if not _CONFIGURED:
        configure_yfinance_cache(cache_dir, max_cache_bytes, max_file_age_days)
    register_yfinance_cache_manager_cleanup()
    return Path(yfc_cache_manager.GetCacheDirpath())


def _resolve_cache_dir(cache_dir: str | Path | None) -> Path:
    override_dir = os.getenv("YFINANCE_CACHE_DIR")
    target_dir = Path(cache_dir or override_dir or yfc_cache_manager.GetCacheDirpath())
    yfc_cache_manager.SetCacheDirpath(str(target_dir))
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def _resolve_size_limit(max_cache_bytes: int | None) -> int:
    env_bytes = os.getenv("YFINANCE_CACHE_MAX_BYTES")
    env_mb = os.getenv("YFINANCE_CACHE_MAX_MB")
    if env_bytes:
        return int(env_bytes)
    if env_mb:
        return int(env_mb) * 1_048_576
    return max_cache_bytes or DEFAULT_MAX_CACHE_BYTES


def _resolve_age_days(max_file_age_days: int) -> int:
    env_age = os.getenv("YFINANCE_CACHE_MAX_AGE_DAYS")
    return int(env_age) if env_age else max_file_age_days


def _prune_yfinance_cache(cache_dir: Path, max_cache_bytes: int, max_file_age_days: int) -> None:
    cutoff = time.time() - max_file_age_days * 86_400 if max_file_age_days > 0 else None
    files: list[tuple[float, Path, int]] = []
    total_size = 0
    for path in cache_dir.rglob("*"):
        if not path.is_file():
            continue
        stats = path.stat()
        if cutoff is not None and stats.st_mtime < cutoff:
            path.unlink(missing_ok=True)
            continue
        files.append((stats.st_mtime, path, stats.st_size))
        total_size += stats.st_size
    if max_cache_bytes <= 0 or total_size <= max_cache_bytes:
        return
    _evict_oldest(files, total_size, max_cache_bytes)


def _evict_oldest(files: list[tuple[float, Path, int]], total_size: int, max_cache_bytes: int) -> None:
    size_remaining = total_size
    for _, file_path, file_size in sorted(files, key=lambda entry: entry[0]):
        if size_remaining <= max_cache_bytes:
            return
        file_path.unlink(missing_ok=True)
        size_remaining -= file_size


def _shutdown_manager() -> None:
    """Shutdown the existing :class:`multiprocessing.Manager` used by yfinance_cache."""
    manager = getattr(yfc_dat, "_manager", None)
    if manager is None:
        return

    manager.shutdown()
    setattr(yfc_dat, "_manager", None)
