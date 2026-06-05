"""Conclusion JSON persistence."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from conclusion_monitor.serialization import json_default


class ConclusionJsonWriter:
    """Write daily conclusion reports to disk."""

    def __init__(self, output_dir: Path | str = "conclusion") -> None:
        self._output_dir = Path(output_dir)

    def write(self, trading_day: date, report: dict[str, Any]) -> Path:
        """Write the report to conclusion/YYYY-MM-DD.json."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self._output_dir / f"{trading_day.isoformat()}.json"
        output_path.write_text(
            json.dumps(report, indent=2, default=json_default, ensure_ascii=True),
            encoding="utf-8",
        )
        return output_path

    def write_range(
        self,
        start_date: date,
        end_date: date,
        report: dict[str, Any],
    ) -> Path:
        """Write the report to conclusion/YYYY-MM-DD_to_YYYY-MM-DD.json."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self._output_dir / (
            f"{start_date.isoformat()}_to_{end_date.isoformat()}.json"
        )
        output_path.write_text(
            json.dumps(report, indent=2, default=json_default, ensure_ascii=True),
            encoding="utf-8",
        )
        return output_path
