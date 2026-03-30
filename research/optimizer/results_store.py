"""Results storage with direction tracking and saturation detection."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from research.optimizer.search_space import hash_params


@dataclass
class ResultsStore:
    """Store optimization results with direction-level tracking."""

    output_dir: Path
    _results: list[dict[str, Any]] = field(default_factory=list)
    _directions: list[dict[str, Any]] = field(default_factory=list)
    _done_fingerprints: set[str] = field(default_factory=set)
    _family_direction_count: dict[str, int] = field(default_factory=dict)
    _params_hashes: set[str] = field(default_factory=set)
    saturation_threshold: int = 50

    def __post_init__(self):
        self.output_dir = Path(self.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._load_existing()

    def _load_existing(self) -> None:
        """Load existing results and directions from disk."""
        results_path = self.output_dir / "results.csv"
        if results_path.exists():
            with open(results_path) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    self._results.append(row)
                    h = row.get("params_hash", "")
                    if h:
                        self._params_hashes.add(h)

        dirs_path = self.output_dir / "directions.json"
        if dirs_path.exists():
            with open(dirs_path) as f:
                data = json.load(f)
                self._directions = data.get("directions", [])
                self._done_fingerprints = set(data.get("done_fingerprints", []))
                self._family_direction_count = data.get("family_counts", {})

    # --- Direction Management ---

    def add_direction(self, direction: dict[str, Any]) -> str:
        """Register a new exploration direction. Returns direction_id."""
        dir_id = f"dir_{len(self._directions):04d}"
        direction["direction_id"] = dir_id
        direction["created_at"] = datetime.utcnow().isoformat()
        direction["status"] = "active"
        direction["experiment_count"] = 0
        direction["good_count"] = 0

        self._directions.append(direction)

        # Track family coverage
        for family in direction.get("primary_families", []):
            self._family_direction_count[family] = self._family_direction_count.get(family, 0) + 1

        self._save_directions()
        return dir_id

    def is_direction_done(self, fingerprint: str) -> bool:
        return fingerprint in self._done_fingerprints

    def finish_direction(self, direction_id: str, summary: dict[str, Any]) -> None:
        """Mark a direction as completed."""
        for d in self._directions:
            if d.get("direction_id") == direction_id:
                d["status"] = "done"
                d.update(summary)
                break

        # Find fingerprint and mark done
        for d in self._directions:
            if d.get("direction_id") == direction_id:
                fp = d.get("fingerprint", "")
                if fp:
                    self._done_fingerprints.add(fp)
                break

        self._save_directions()

    def get_family_counts(self) -> dict[str, int]:
        """Return direction count per factor family."""
        return self._family_direction_count.copy()

    # --- Result Management ---

    def add(self, params: dict[str, Any], evaluation: dict[str, Any],
            direction_id: str = "") -> None:
        """Add an experiment result."""
        ph = hash_params(params)
        record = {
            "direction_id": direction_id,
            "params_hash": ph,
            "status": evaluation.get("status", "ok"),
            "sharpe": evaluation.get("sharpe", 0),
            "max_drawdown": evaluation.get("max_drawdown", 0),
            "win_rate": evaluation.get("win_rate", 0),
            "profit_factor": evaluation.get("profit_factor", 0),
            "net_pnl_pct": evaluation.get("net_pnl_pct", 0),
            "composite_score": evaluation.get("composite_score", 0),
            "quality_score": evaluation.get("quality_score", 0),
            "is_good": evaluation.get("is_good", False),
            "checks_passed": evaluation.get("checks_passed", 0),
            "created_at": datetime.utcnow().isoformat(),
            "params_json": json.dumps(params, default=str),
        }

        self._results.append(record)
        self._params_hashes.add(ph)

        # Update direction stats
        for d in self._directions:
            if d.get("direction_id") == direction_id:
                d["experiment_count"] = d.get("experiment_count", 0) + 1
                if evaluation.get("is_good"):
                    d["good_count"] = d.get("good_count", 0) + 1
                break

        self._append_result_csv(record)

    @staticmethod
    def _is_good(r: dict[str, Any]) -> bool:
        """Check if a result is 'good', handling both bool and string values."""
        val = r.get("is_good", False)
        if isinstance(val, bool):
            return val
        return str(val) == "True"

    def exists(self, params: dict[str, Any]) -> bool:
        """Check if params already tested."""
        return hash_params(params) in self._params_hashes

    def get_history(self) -> list[dict[str, Any]]:
        return self._results.copy()

    def total_count(self) -> int:
        return len(self._results)

    def good_count(self) -> int:
        return sum(1 for r in self._results if self._is_good(r))

    def is_saturated(self) -> bool:
        return self.good_count() >= self.saturation_threshold

    def get_best(self) -> dict[str, Any] | None:
        if not self._results:
            return None
        return max(self._results, key=lambda r: float(r.get("quality_score", -999)))

    def get_top_k(self, k: int = 5) -> list[dict[str, Any]]:
        """Get top-k results by quality_score."""
        valid = [r for r in self._results if float(r.get("quality_score", -999)) > -999]
        return sorted(valid, key=lambda r: float(r.get("quality_score", -999)), reverse=True)[:k]

    def get_saturated_factors(self, threshold: int = 50) -> dict[str, int]:
        """Return factor families with >= threshold good strategies."""
        family_good = {}
        for r in self._results:
            if self._is_good(r):
                try:
                    params = json.loads(r.get("params_json", "{}"))
                except (json.JSONDecodeError, TypeError):
                    continue
                families = params.get("primary_families", [])
                for f in families:
                    family_good[f] = family_good.get(f, 0) + 1
        return {k: v for k, v in family_good.items() if v >= threshold}

    def get_direction_results(self, direction_id: str) -> list[dict[str, Any]]:
        """Get results for a specific direction."""
        return [r for r in self._results if r.get("direction_id") == direction_id]

    # --- Persistence ---

    def save_summary(self) -> None:
        """Save final summary files."""
        self._save_directions()

        best = self.get_best()
        if best:
            with open(self.output_dir / "best_params.json", "w") as f:
                json.dump(best, f, indent=2, default=str)

        # Save good results separately
        good = [r for r in self._results if self._is_good(r)]
        if good:
            with open(self.output_dir / "good_results.json", "w") as f:
                json.dump(good, f, indent=2, default=str)

    def _save_directions(self) -> None:
        with open(self.output_dir / "directions.json", "w") as f:
            json.dump({
                "directions": self._directions,
                "done_fingerprints": list(self._done_fingerprints),
                "family_counts": self._family_direction_count,
            }, f, indent=2, default=str)

    def _append_result_csv(self, record: dict[str, Any]) -> None:
        """Append one result to CSV (create header if needed)."""
        csv_path = self.output_dir / "results.csv"
        write_header = not csv_path.exists()

        fields = ["direction_id", "params_hash", "status", "sharpe", "max_drawdown",
                   "win_rate", "profit_factor", "net_pnl_pct", "composite_score",
                   "quality_score", "is_good", "checks_passed", "created_at", "params_json"]

        with open(csv_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerow(record)
