"""LLM-driven parameter generation for crypto strategy optimization."""
from __future__ import annotations

import json
import os
import random
import traceback
from dataclasses import dataclass, field
from typing import Any, Optional

from research.optimizer.search_space import (
    SEARCH_DIMENSIONS,
    ALL_PARAM_NAMES,
    compute_direction_fingerprint,
    hash_params,
    validate_params,
    describe_search_space,
)

# Token usage tracking
_token_usage = {"input": 0, "output": 0, "calls": 0}


def _load_llm_config() -> dict[str, str]:
    """Load LLM config from trading_params.json (V9) or env vars.

    Priority: env vars > trading_params.json
    """
    config = {"api_key": "", "base_url": "", "model": ""}

    # Try loading from trading_params.json
    try:
        import json as _json
        from pathlib import Path
        params_path = Path(__file__).resolve().parents[2] / "config" / "trading_params.json"
        if params_path.exists():
            with open(params_path) as f:
                data = _json.load(f)
            active = data.get("active_version", "V9")
            v = data.get("versions", {}).get(active, {})
            endpoint = v.get("AI_LLM_ENDPOINT", "")
            if endpoint:
                # Strip /chat/completions to get base URL for OpenAI SDK
                # e.g. https://coding.dashscope.aliyuncs.com/v1/chat/completions → .../v1
                config["base_url"] = endpoint.rsplit("/chat/completions", 1)[0]
            config["api_key"] = v.get("AI_LLM_API_KEY", "")
            config["model"] = v.get("AI_LLM_MODEL", "")
    except Exception:
        pass

    # Env vars override
    if os.environ.get("LLM_API_KEY"):
        config["api_key"] = os.environ["LLM_API_KEY"]
    if os.environ.get("LLM_BASE_URL"):
        config["base_url"] = os.environ["LLM_BASE_URL"]
    if os.environ.get("LLM_MODEL"):
        config["model"] = os.environ["LLM_MODEL"]

    return config


def _get_client():
    """Create OpenAI-compatible client.

    Auto-loads config from config/trading_params.json (active version).
    Env vars LLM_API_KEY / LLM_BASE_URL / LLM_MODEL override if set.
    """
    cfg = _load_llm_config()
    api_key = cfg["api_key"]
    if not api_key:
        return None
    try:
        from openai import OpenAI
        return OpenAI(
            api_key=api_key,
            base_url=cfg["base_url"] or "https://api.openai.com/v1",
        )
    except ImportError:
        return None


def _call_llm(prompt: str, model: str = "", max_tokens: int = 4096) -> str:
    """Call LLM via OpenAI-compatible API. Tracks token usage."""
    global _token_usage

    if not model:
        cfg = _load_llm_config()
        model = cfg["model"] or "gpt-4o-mini"

    client = _get_client()
    if client is None:
        raise RuntimeError(
            "openai package not installed or LLM_API_KEY not set.\n"
            "Required env vars:\n"
            "  LLM_API_KEY   — API key\n"
            "  LLM_BASE_URL  — API base URL (e.g. https://coding.dashscope.aliyuncs.com/compatible-mode/v1)\n"
            "  LLM_MODEL     — Model name (e.g. qwen3-max-2026-01-23)"
        )

    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )

    if response.usage:
        _token_usage["input"] += response.usage.prompt_tokens
        _token_usage["output"] += response.usage.completion_tokens
    _token_usage["calls"] += 1

    return response.choices[0].message.content or ""


def _extract_json(text: str) -> Any:
    """Extract JSON from LLM response (handles code blocks, markdown)."""
    # Try direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # Try code block
    import re
    for pattern in [r"```json\s*\n(.*?)```", r"```\s*\n(.*?)```"]:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                continue

    # Try finding array or object
    for start, end in [("[", "]"), ("{", "}")]:
        idx_start = text.find(start)
        idx_end = text.rfind(end)
        if idx_start >= 0 and idx_end > idx_start:
            try:
                return json.loads(text[idx_start:idx_end + 1])
            except json.JSONDecodeError:
                continue

    return None


def get_token_usage() -> dict:
    return _token_usage.copy()


@dataclass
class ParamGenerator:
    """Two-layer LLM-driven parameter generator for crypto strategy optimization.

    Layer 1 (pick_direction): Select factor families + parameter dimensions to explore
    Layer 2 (generate_round): Generate specific parameter sets within a direction
    """

    search_dims: dict[str, Any] | None = None
    experiments_per_round: int = 8
    model: str = ""

    def pick_direction(
        self,
        explored_directions: list[str],
        top_results: list[dict[str, Any]],
        family_counts: dict[str, int],
        saturated_factors: dict[str, int],
    ) -> dict[str, Any] | None:
        """Use LLM to pick the next exploration direction.

        Returns dict with: description, primary_families, strategy_type,
        focus_dimensions, fingerprint. Or None on failure.
        """
        search_desc = describe_search_space()
        n_explored = len(explored_directions)

        # Phase detection
        if n_explored < 15:
            phase = "Phase 1: Single-family exploration. Test each factor family independently."
            phase_guidance = "Pick ONE factor family that has been least explored. Use weighted_factor strategy with factors from that family only."
        elif n_explored < 40:
            phase = "Phase 2: Cross-family combinations. Combine factors from 2 different families."
            phase_guidance = "Pick TWO factor families and combine their factors. Focus on families that showed promise in Phase 1."
        else:
            phase = "Phase 3: Deep optimization. Focus on the most promising combinations."
            phase_guidance = "Focus on parameter dimensions (position, timing, layer1) around the best-performing factor combinations."

        # Format top results for context (values may be strings from CSV)
        top_str = "No results yet." if not top_results else "\n".join(
            f"  #{i+1}: sharpe={float(r.get('sharpe', 0)):.2f}, max_dd={float(r.get('max_drawdown', 0)):.2%}, "
            f"win_rate={float(r.get('win_rate', 0)):.2%}, params={json.dumps(r.get('params', {}), default=str)[:200]}"
            for i, r in enumerate(top_results[:5])
        )

        # Format exploration coverage
        coverage_str = "\n".join(
            f"  {family}: {count} directions explored"
            for family, count in sorted(family_counts.items(), key=lambda x: x[1])
        )

        saturated_str = ", ".join(saturated_factors.keys()) if saturated_factors else "none"

        prompt = f"""You are optimizing a crypto perpetual futures trading strategy on Hyperliquid (BTC/ETH).

{search_desc}

## Current State
- Directions explored: {n_explored}
- {phase}
- Saturated factor families (de-prioritize): {saturated_str}

## Family Exploration Coverage:
{coverage_str}

## Top 5 Results So Far:
{top_str}

## Your Task
{phase_guidance}

Return a JSON object with:
- "description": one-line description of what to explore
- "primary_families": list of 1-2 factor family names to focus on
- "strategy_type": "single_factor" or "weighted_factor"
- "focus_dimensions": list of parameter dimension names to vary (e.g. ["signal_params", "position_params"])

Return ONLY the JSON object, no other text.
"""
        try:
            response = _call_llm(prompt, self.model)
            direction = _extract_json(response)
            if not isinstance(direction, dict):
                return self._random_direction()

            direction["fingerprint"] = compute_direction_fingerprint(direction)
            return direction

        except Exception as e:
            print(f"  LLM pick_direction failed: {e}")
            return self._random_direction()

    def generate_round(
        self,
        direction: dict[str, Any],
        round_num: int,
        previous_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Generate parameter sets for one round within a direction.

        Round 1: Initial exploration with controlled variable testing
        Round 2+: Refine around best results from previous rounds
        Returns [] if direction is exhausted.
        """
        families = direction.get("primary_families", [])
        strategy_type = direction.get("strategy_type", "weighted_factor")
        focus_dims = direction.get("focus_dimensions", ["signal_params", "position_params"])

        # Build parameter ranges for focused dimensions
        dim_ranges = {}
        for dim_name in focus_dims:
            if dim_name in SEARCH_DIMENSIONS:
                dim_ranges[dim_name] = SEARCH_DIMENSIONS[dim_name]

        # Get available factors for the families
        available_factors = []
        all_families = SEARCH_DIMENSIONS["active_factors"]["families"]
        for family in families:
            if family in all_families:
                available_factors.extend(all_families[family])

        if not available_factors:
            available_factors = ["book_imbalance_l5", "aggr_delta_5m"]

        if round_num == 1:
            return self._generate_initial_round(
                available_factors, strategy_type, dim_ranges, direction
            )
        else:
            return self._generate_refinement_round(
                available_factors, strategy_type, dim_ranges, direction,
                round_num, previous_results
            )

    def _generate_initial_round(
        self,
        factors: list[str],
        strategy_type: str,
        dim_ranges: dict,
        direction: dict,
    ) -> list[dict[str, Any]]:
        """Round 1: LLM generates baseline + controlled experiments."""

        dim_desc = json.dumps(dim_ranges, indent=2, default=str)

        prompt = f"""You are designing backtest experiments for a crypto perpetual futures strategy.

## Direction: {direction.get('description', 'unknown')}

## Available Factors: {', '.join(factors)}
## Strategy Type: {strategy_type}
## Parameter Dimensions to Vary:
{dim_desc}

## Experiment Design Rules:
1. Generate {self.experiments_per_round} experiments
2. Start with a BASELINE using default/middle values for all parameters
3. Then vary ONE dimension at a time while keeping others at baseline:
   - Test 2-3 values for profit_target_bps (e.g., 15, 25, 40)
   - Test 2-3 values for stop_loss_bps (e.g., 20, 40, 60)
   - Test 2-3 values for min_trade_interval_min (e.g., 30, 60, 90)
4. Each experiment must have:
   - "strategy_type": "{strategy_type}"
   - "factor_name" (for single_factor) or "factor_weights" dict (for weighted_factor)
   - Any parameters from the dimensions above

## Factor Weights (for weighted_factor):
Assign weights that sum to 1.0. Example: {{"book_imbalance_l5": 0.4, "aggr_delta_5m": 0.3, "funding_rate": 0.3}}

Return a JSON array of {self.experiments_per_round} experiment objects. Each object has string/number fields only.
Return ONLY the JSON array.
"""
        try:
            response = _call_llm(prompt, self.model)
            experiments = _extract_json(response)
            if isinstance(experiments, list):
                valid = []
                for exp in experiments:
                    if isinstance(exp, dict):
                        ok, err = validate_params(exp)
                        if ok:
                            valid.append(exp)
                return valid if valid else self._random_experiments(factors, strategy_type)
            return self._random_experiments(factors, strategy_type)
        except Exception as e:
            print(f"  LLM generate_round failed: {e}")
            return self._random_experiments(factors, strategy_type)

    def _generate_refinement_round(
        self,
        factors: list[str],
        strategy_type: str,
        dim_ranges: dict,
        direction: dict,
        round_num: int,
        previous_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Round 2+: LLM refines around best previous results."""

        if not previous_results:
            return []

        # Sort by quality_score
        sorted_results = sorted(previous_results, key=lambda r: r.get("quality_score", -999), reverse=True)

        # Check for improvement stalling
        if round_num >= 3:
            recent = sorted_results[:3]
            if all(r.get("quality_score", -999) < 0 for r in recent):
                return []  # Direction exhausted

        results_str = "\n".join(
            f"  Experiment: sharpe={float(r.get('sharpe', 0)):.2f}, max_dd={float(r.get('max_drawdown', 0)):.2%}, "
            f"win_rate={float(r.get('win_rate', 0)):.2%}, quality={float(r.get('quality_score', 0)):.3f}, "
            f"params={json.dumps(r.get('params', {}), default=str)[:300]}"
            for r in sorted_results[:5]
        )

        prompt = f"""You are refining a crypto trading strategy. Round {round_num} of exploration.

## Direction: {direction.get('description', '')}
## Available Factors: {', '.join(factors)}
## Strategy: {strategy_type}

## Previous Results (sorted by quality, best first):
{results_str}

## Your Task:
1. Analyze which parameter values performed best
2. Generate {self.experiments_per_round} new experiments that:
   - Micro-vary parameters around the best-performing values
   - Test 2-3 small perturbations of the best params
   - Try at most ONE new parameter change per experiment
3. Return [] (empty array) if you believe further refinement won't help

Return ONLY a JSON array of experiment objects (or empty []).
"""
        try:
            response = _call_llm(prompt, self.model)
            experiments = _extract_json(response)
            if isinstance(experiments, list):
                if len(experiments) == 0:
                    return []
                valid = []
                for exp in experiments:
                    if isinstance(exp, dict):
                        ok, err = validate_params(exp)
                        if ok:
                            valid.append(exp)
                return valid if valid else []
            return []
        except Exception as e:
            print(f"  LLM refinement failed: {e}")
            return []

    def generate(self, history: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """Simple interface: generate a batch using random sampling (no LLM).

        For LLM-driven search, use pick_direction() + generate_round() instead.
        """
        return self._random_experiments(
            ["book_imbalance_l5", "aggr_delta_5m", "funding_rate"],
            "weighted_factor",
        )

    def _random_direction(self) -> dict[str, Any]:
        """Fallback: random direction when LLM fails."""
        families = list(SEARCH_DIMENSIONS["active_factors"]["families"].keys())
        family = random.choice(families)
        dims = ["signal_params", "position_params"]
        direction = {
            "description": f"Random exploration of {family}",
            "primary_families": [family],
            "strategy_type": "weighted_factor",
            "focus_dimensions": dims,
        }
        direction["fingerprint"] = compute_direction_fingerprint(direction)
        return direction

    def _random_experiments(
        self, factors: list[str], strategy_type: str
    ) -> list[dict[str, Any]]:
        """Fallback: random parameter sampling."""
        batch = []
        for _ in range(self.experiments_per_round):
            params: dict[str, Any] = {"strategy_type": strategy_type}

            if strategy_type == "single_factor":
                params["factor_name"] = random.choice(factors)
            else:
                n = min(len(factors), random.randint(2, 4))
                selected = random.sample(factors, n)
                weights = [random.random() for _ in selected]
                total = sum(weights)
                params["factor_weights"] = {f: round(w / total, 2) for f, w in zip(selected, weights)}

            for dim_name, dim_spec in SEARCH_DIMENSIONS.items():
                if dim_name == "active_factors":
                    continue
                if isinstance(dim_spec, dict):
                    for param_name, param_range in dim_spec.items():
                        if isinstance(param_range, dict) and "min" in param_range:
                            if random.random() < 0.3:  # Only vary 30% of params
                                step = param_range.get("step", 1)
                                val = random.uniform(param_range["min"], param_range["max"])
                                params[param_name] = round(val / step) * step
                        elif isinstance(param_range, list):
                            if random.random() < 0.3:
                                params[param_name] = random.choice(param_range)

            batch.append(params)
        return batch
