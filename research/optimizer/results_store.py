"""Results storage for parameter optimization experiments."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ResultsStore:
    """Store and query optimization experiment results."""

    output_dir: Path
    _results: list[dict[str, Any]] = field(default_factory=list)
    saturation_threshold: int = 50

    def __post_init__(self):
        self.output_dir = Path(self.output_dir)

    def add(self, params: dict[str, Any], evaluation: dict[str, Any]) -> None:
        self._results.append({"params": params, **evaluation})

    def get_history(self) -> list[dict[str, Any]]:
        return self._results.copy()

    def total_count(self) -> int:
        return len(self._results)

    def good_count(self) -> int:
        return sum(1 for r in self._results if r.get("is_good"))

    def is_saturated(self) -> bool:
        return self.good_count() >= self.saturation_threshold

    def get_best(self) -> dict[str, Any] | None:
        if not self._results:
            return None
        return max(self._results, key=lambda r: r.get("quality_score", -999))

    def save_summary(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Save all results
        with open(self.output_dir / "search_results.csv", "w") as f:
            if self._results:
                keys = [k for k in self._results[0] if k != "params"]
                f.write(",".join(keys) + "\n")
                for r in self._results:
                    f.write(",".join(str(r.get(k, "")) for k in keys) + "\n")

        # Save best params
        best = self.get_best()
        if best:
            with open(self.output_dir / "best_params.json", "w") as f:
                json.dump(best, f, indent=2, default=str)
