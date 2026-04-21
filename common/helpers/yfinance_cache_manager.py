"""Local cache helpers for yfinance usage and market sessions."""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

import exchange_calendars as xcals


DEFAULT_MAX_CACHE_BYTES: Final[int] = 2 * 1024 * 1024 * 1024  # 2 GiB ceiling
DEFAULT_MAX_FILE_AGE_DAYS: Final[int] = 30
DEFAULT_MARKET_CACHE_FILENAME: Final[str] = "market_hours_xnys.json"
_CONFIGURED = False


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
    """Prepare local cache directory for plain yfinance usage."""
    if not _CONFIGURED:
        configure_yfinance_cache(cache_dir, max_cache_bytes, max_file_age_days)
    return _resolve_cache_dir(cache_dir)


def _resolve_cache_dir(cache_dir: str | Path | None) -> Path:
    override_dir = os.getenv("YFINANCE_CACHE_DIR")
    target_dir = Path(cache_dir or override_dir or ".cache/yfinance")
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


def get_cached_market_session_hours(
    trading_day: date,
    exchange: str = "XNYS",
    timezone: str = "America/New_York",
) -> tuple[datetime, datetime]:
    """Get market open/close from local cache; compute and persist on cache miss."""
    cache_file = _resolve_cache_dir(None) / DEFAULT_MARKET_CACHE_FILENAME
    cache = _read_market_hours_cache(cache_file)
    key = trading_day.isoformat()

    if key in cache:
        cached_entry = cache[key]
        return (
            datetime.fromisoformat(cached_entry["open"]),
            datetime.fromisoformat(cached_entry["close"]),
        )

    calendar = xcals.get_calendar(exchange)
    session = _resolve_session_timestamp(calendar, trading_day)
    tz = ZoneInfo(timezone)
    session_open = calendar.session_open(session).tz_convert(tz).to_pydatetime()
    session_close = calendar.session_close(session).tz_convert(tz).to_pydatetime()

    cache[key] = {"open": session_open.isoformat(), "close": session_close.isoformat()}
    _write_market_hours_cache(cache_file, cache)
    return session_open, session_close


def _resolve_session_timestamp(calendar: xcals.ExchangeCalendar, trading_day: date):
    for session in calendar.sessions:
        if session.date() >= trading_day:
            return session
    return calendar.sessions[-1]


def _read_market_hours_cache(cache_file: Path) -> dict[str, dict[str, str]]:
    if not cache_file.exists():
        return {}
    try:
        return json.loads(cache_file.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_market_hours_cache(cache_file: Path, cache: dict[str, dict[str, str]]) -> None:
    cache_file.write_text(json.dumps(cache, indent=2), encoding="utf-8")
