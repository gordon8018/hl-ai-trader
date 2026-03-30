"""Parameter generation for optimization search (stub for LLM integration)."""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any


@dataclass
class ParamGenerator:
    """Generate parameter combinations for backtesting.

    Currently uses random sampling. Will be upgraded to LLM-driven search
    from feature_factory/analysis/agent/param_generator.py.
    """

    search_dims: dict[str, Any] | None = None
    experiments_per_round: int = 5

    def generate(self, history: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """Generate a batch of parameter combinations."""
        if not self.search_dims:
            return [{}]

        batch = []
        for _ in range(self.experiments_per_round):
            params = {}
            for dim_name, dim_spec in self.search_dims.items():
                if dim_name == "active_factors":
                    continue  # Skip factor selection for now
                if isinstance(dim_spec, dict):
                    for param_name, param_range in dim_spec.items():
                        if isinstance(param_range, dict) and "min" in param_range:
                            step = param_range.get("step", 1)
                            val = random.uniform(param_range["min"], param_range["max"])
                            val = round(val / step) * step
                            params[param_name] = val
                        elif isinstance(param_range, list):
                            params[param_name] = random.choice(param_range)
            batch.append(params)
        return batch
