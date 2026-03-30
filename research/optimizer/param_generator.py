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
    cfg = _load_llm_config()
    api_key = cfg["api_key"]
    base_url = cfg["base_url"] or "https://api.openai.com/v1"
    if not api_key:
        raise RuntimeError(
            f"LLM API key not found.\n"
            f"  Checked trading_params.json AI_LLM_API_KEY and env LLM_API_KEY — both empty.\n"
            f"  Set LLM_API_KEY env var or configure AI_LLM_API_KEY in config/trading_params.json"
        )
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError(f"openai package not installed: {e}\nRun: pip install openai") from e
    return OpenAI(api_key=api_key, base_url=base_url)


def _call_llm(prompt: str, model: str = "", max_tokens: int = 4096) -> str:
    """Call LLM via OpenAI-compatible API. Tracks token usage."""
    global _token_usage

    if not model:
        cfg = _load_llm_config()
        model = cfg["model"] or "gpt-4o-mini"

    client = _get_client()

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
        from research.optimizer.search_space import get_available_families
        avail_families = get_available_families()
        search_desc = describe_search_space()
        avail_note = "\n### AVAILABLE factors (have data):\n" + "\n".join(
            f"- **{fam}**: {', '.join(factors)}" for fam, factors in avail_families.items()
        ) + "\n\n**ONLY use families listed above. Other families have no data.**"
        search_desc += "\n" + avail_note
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

        # Strategy-specific parameter guidance
        if strategy_type == "single_factor":
            strategy_params = """## Parameters that ACTUALLY affect single_factor strategy:
- "factor_name": which factor to trade on (MUST vary across experiments!)
- "long_pct": percentile threshold for going long (0.6-0.9, higher = fewer trades)
- "short_pct": percentile threshold for going short (0.1-0.4, lower = fewer trades)
- "max_gross": max position weight (0.10-0.50)

IMPORTANT: Each experiment MUST have a DIFFERENT "factor_name" or different thresholds!"""

        elif strategy_type == "rule_based":
            strategy_params = """## Parameters that ACTUALLY affect rule_based strategy:
- "profit_target_bps": take profit in basis points (10-50)
- "stop_loss_bps": stop loss in basis points (15-80)
- "min_trade_interval_min": minimum minutes between trades (15-120)
- "max_trades_per_day": daily trade cap (5-30)
- "position_max_age_min": close stale positions after N minutes (15-120)
- "trending_strength_threshold": minimum trend strength to trade (0.5-2.0)
- "max_gross": max position weight (0.10-0.50)
- "bearish_regime_long_block": block longs in bearish regime (true/false)

IMPORTANT: Vary profit_target_bps, stop_loss_bps, and timing params across experiments!"""

        else:  # weighted_factor
            strategy_params = f"""## Parameters that ACTUALLY affect weighted_factor strategy:
- "factor_weights": dict mapping factor names to weights (MUST vary across experiments!)
  Available factors: {', '.join(factors)}
  Weights should sum to 1.0.
- "signal_delta_threshold": z-score threshold for entry (0.3-1.5, higher = fewer trades)
- "max_gross": max position weight (0.10-0.50)

CRITICAL: Each experiment MUST have DIFFERENT "factor_weights" combinations!
Example variations:
  - Exp 1: {{"ret_15m": 0.5, "vol_15m": 0.5}}
  - Exp 2: {{"ret_15m": 0.3, "trend_agree": 0.4, "funding_rate": 0.3}}
  - Exp 3: {{"spread_bps": 0.6, "ret_1h": 0.4}}
  - Exp 4: {{"vol_15m": 0.7, "trend_strength_15m": 0.3}} with signal_delta_threshold=0.8"""

        prompt = f"""You are designing backtest experiments for a crypto perpetual futures strategy.

## Direction: {direction.get('description', 'unknown')}

{strategy_params}

## Experiment Design Rules:
1. Generate {self.experiments_per_round} experiments as a JSON array
2. Each experiment MUST be meaningfully different from the others
3. Always include "strategy_type": "{strategy_type}"
4. Vary the parameters listed above — other parameters are IGNORED

Return ONLY a JSON array of {self.experiments_per_round} objects.
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

        # Only give up after round 4 with no improvement
        if round_num >= 4:
            best = sorted_results[0].get("quality_score", -999)
            if best < -20:
                return []  # Direction truly exhausted

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

## IMPORTANT: For {strategy_type} strategy, only these params matter:
{self._get_effective_params_hint(strategy_type, factors)}

## Your Task:
1. Look at which experiments had the LEAST negative quality score
2. Generate {self.experiments_per_round} new experiments varying the effective params above
3. Try different factor combinations, different thresholds, different position sizes
4. Always include "strategy_type": "{strategy_type}"
5. Even if all results are negative, keep exploring — different factor combos can help

Return ONLY a JSON array of experiment objects. Do NOT return empty [].
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

    @staticmethod
    def _get_effective_params_hint(strategy_type: str, factors: list[str]) -> str:
        if strategy_type == "single_factor":
            return (f"- factor_name: one of {factors}\n"
                    "- long_pct: 0.6-0.9\n- short_pct: 0.1-0.4\n- max_gross: 0.10-0.50")
        elif strategy_type == "rule_based":
            return ("- profit_target_bps: 10-50\n- stop_loss_bps: 15-80\n"
                    "- min_trade_interval_min: 15-120\n- max_trades_per_day: 5-30\n"
                    "- trending_strength_threshold: 0.5-2.0\n- max_gross: 0.10-0.50")
        else:
            return (f"- factor_weights: dict with keys from {factors}, values sum to 1.0\n"
                    "- signal_delta_threshold: 0.3-1.5\n- max_gross: 0.10-0.50")

    def generate(self, history: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """Simple interface: generate a batch using random sampling (no LLM).

        For LLM-driven search, use pick_direction() + generate_round() instead.
        """
        from research.optimizer.search_space import get_available_families
        avail = get_available_families()
        all_factors = [f for factors in avail.values() for f in factors]
        if not all_factors:
            all_factors = ["ret_15m", "vol_15m", "spread_bps"]
        return self._random_experiments(all_factors, "weighted_factor")

    def _random_direction(self) -> dict[str, Any]:
        """Fallback: random direction when LLM fails. Only uses families with available data."""
        from research.optimizer.search_space import get_available_families
        avail = get_available_families()
        if not avail:
            avail = {"momentum": ["ret_15m", "ret_1h"]}

        family = random.choice(list(avail.keys()))
        # Mix strategies: 40% single_factor, 40% weighted_factor, 20% rule_based
        r = random.random()
        if r < 0.4:
            strategy = "single_factor"
        elif r < 0.8:
            strategy = "weighted_factor"
        else:
            strategy = "rule_based"

        dims = random.sample(
            ["signal_params", "position_params", "timing_params", "layer1_params"],
            k=random.randint(1, 3),
        )
        direction = {
            "description": f"Random exploration of {family} ({strategy})",
            "primary_families": [family],
            "strategy_type": strategy,
            "focus_dimensions": dims,
        }
        direction["fingerprint"] = compute_direction_fingerprint(direction)
        return direction

    def _random_experiments(
        self, factors: list[str], strategy_type: str
    ) -> list[dict[str, Any]]:
        """Fallback: generate diverse random experiments.

        Each experiment varies factor selection AND key parameters to ensure
        different outcomes (not 8 identical results).
        """
        # Key params to vary across experiments for diversity
        gross_values = [0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]
        threshold_values = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
        pt_values = [10, 15, 20, 25, 30, 40, 50]
        sl_values = [15, 20, 30, 40, 50, 60, 80]

        batch = []
        for i in range(self.experiments_per_round):
            params: dict[str, Any] = {"strategy_type": strategy_type}

            if strategy_type == "single_factor":
                # Each experiment uses a different factor
                params["factor_name"] = factors[i % len(factors)]
                params["long_pct"] = random.choice([0.6, 0.65, 0.7, 0.75, 0.8])
                params["short_pct"] = random.choice([0.2, 0.25, 0.3, 0.35, 0.4])
            elif strategy_type == "rule_based":
                params["profit_target_bps"] = pt_values[i % len(pt_values)]
                params["stop_loss_bps"] = sl_values[i % len(sl_values)]
                params["min_trade_interval_min"] = random.choice([15, 30, 45, 60, 90])
                params["max_trades_per_day"] = random.choice([5, 10, 15, 20, 30])
                params["trending_strength_threshold"] = random.choice([0.5, 0.75, 1.0, 1.25, 1.5])
            else:  # weighted_factor
                # Vary number and selection of factors
                n = min(len(factors), random.randint(2, min(5, len(factors))))
                selected = random.sample(factors, n)
                weights = [random.random() for _ in selected]
                total = sum(weights)
                params["factor_weights"] = {f: round(w / total, 2) for f, w in zip(selected, weights)}

            # Vary key position params — each experiment gets different values
            params["max_gross"] = gross_values[i % len(gross_values)]
            params["signal_delta_threshold"] = threshold_values[i % len(threshold_values)]

            batch.append(params)
        return batch
