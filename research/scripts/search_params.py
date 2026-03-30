"""CLI: LLM-driven parameter optimization search."""
from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path


_shutdown = False

def _handle_sigint(sig, frame):
    global _shutdown
    _shutdown = True
    print("\nShutdown requested, finishing current experiment...")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="LLM-driven parameter search")
    parser.add_argument("--num-workers", type=int, default=1)
    parser.add_argument("--max-directions", type=int, default=20)
    parser.add_argument("--max-rounds", type=int, default=5,
                        help="Max rounds per direction")
    parser.add_argument("--experiments-per-round", type=int, default=8)
    parser.add_argument("--data-dir", default="research/data/parquet")
    parser.add_argument("--output-dir", default="research/reports/optimizer")
    parser.add_argument("--model", default="", help="LLM model override")
    parser.add_argument("--no-llm", action="store_true",
                        help="Use random search instead of LLM")
    parser.add_argument("--status", action="store_true",
                        help="Show status and exit")
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)

    from research.optimizer.results_store import ResultsStore
    from research.optimizer.param_generator import ParamGenerator, get_token_usage
    from research.optimizer.executor import ExperimentExecutor
    from research.optimizer.evaluator import CryptoEvaluator
    from research.optimizer.search_space import GOOD_THRESHOLDS

    store = ResultsStore(output_dir=output_dir)

    if args.status:
        print(f"Total experiments: {store.total_count()}")
        print(f"Good strategies: {store.good_count()}")
        print(f"Directions: {len(store._directions)}")
        top = store.get_top_k(5)
        if top:
            print("\nTop 5:")
            for i, r in enumerate(top):
                print(f"  #{i+1}: quality={float(r.get('quality_score', 0)):.3f}, "
                      f"sharpe={float(r.get('sharpe', 0)):.2f}, "
                      f"max_dd={float(r.get('max_drawdown', 0)):.2%}")
        return

    signal.signal(signal.SIGINT, _handle_sigint)

    generator = ParamGenerator(
        experiments_per_round=args.experiments_per_round,
        model=args.model,
    )
    executor = ExperimentExecutor(data_dir=args.data_dir, num_workers=args.num_workers)
    evaluator = CryptoEvaluator(thresholds=GOOD_THRESHOLDS)

    print(f"Starting parameter search")
    print(f"  Max directions: {args.max_directions}")
    print(f"  Max rounds/direction: {args.max_rounds}")
    print(f"  Experiments/round: {args.experiments_per_round}")
    print(f"  Mode: {'random' if args.no_llm else 'LLM-driven'}")
    print(f"  Data: {args.data_dir}")
    print(f"  Output: {output_dir}")
    print()

    for dir_num in range(1, args.max_directions + 1):
        if _shutdown or store.is_saturated():
            break

        # Layer 1: Pick direction
        print(f"=== Direction {dir_num}/{args.max_directions} ===")

        if args.no_llm:
            direction = generator._random_direction()
        else:
            # Retry up to 5 times to avoid duplicate fingerprints
            direction = None
            for attempt in range(5):
                candidate = generator.pick_direction(
                    explored_directions=list(store._done_fingerprints),
                    top_results=store.get_top_k(5),
                    family_counts=store.get_family_counts(),
                    saturated_factors=store.get_saturated_factors(),
                )
                if candidate and not store.is_direction_done(candidate.get("fingerprint", "")):
                    direction = candidate
                    break

            if direction is None:
                direction = generator._random_direction()

        dir_id = store.add_direction(direction)
        print(f"  Direction: {direction.get('description', 'unknown')}")
        print(f"  Families: {direction.get('primary_families', [])}")
        print(f"  Strategy: {direction.get('strategy_type', 'unknown')}")

        # Layer 2: Explore direction across rounds
        all_round_results = []

        for round_num in range(1, args.max_rounds + 1):
            if _shutdown:
                break

            print(f"\n  Round {round_num}/{args.max_rounds}:")

            if args.no_llm:
                families = direction.get("primary_families", [])
                from research.optimizer.search_space import SEARCH_DIMENSIONS
                factors = []
                all_families = SEARCH_DIMENSIONS["active_factors"]["families"]
                for fam in families:
                    if fam in all_families:
                        factors.extend(all_families[fam])
                if not factors:
                    factors = ["book_imbalance_l5"]
                params_batch = generator._random_experiments(
                    factors, direction.get("strategy_type", "weighted_factor")
                )
            else:
                params_batch = generator.generate_round(
                    direction, round_num, all_round_results
                )

            if not params_batch:
                print("    Direction exhausted")
                break

            # Deduplicate
            unique_params = []
            for params in params_batch:
                if not store.exists(params):
                    unique_params.append(params)

            if not unique_params:
                print("    All experiments already tested")
                continue

            print(f"    Running {len(unique_params)} experiments...")

            # Execute
            results = executor.run_batch(unique_params)

            # Evaluate and store
            round_good = 0
            for params, result in zip(unique_params, results):
                evaluation = evaluator.evaluate(result)
                store.add(params, evaluation, direction_id=dir_id)
                all_round_results.append(evaluation)

                status_marker = "+" if evaluation.get("is_good") else " "
                print(f"    [{status_marker}] sharpe={evaluation.get('sharpe', 0):.2f}, "
                      f"max_dd={evaluation.get('max_drawdown', 0):.2%}, "
                      f"quality={evaluation.get('quality_score', 0):.3f}")

                if evaluation.get("is_good"):
                    round_good += 1

            if round_good > 0:
                print(f"    {round_good} good strategies found!")

        # Finish direction
        dir_results = store.get_direction_results(dir_id)
        dir_good = sum(1 for r in dir_results if store._is_good(r))
        store.finish_direction(dir_id, {
            "experiment_count": len(dir_results),
            "good_count": dir_good,
        })

        print(f"\n  Direction complete: {len(dir_results)} experiments, {dir_good} good")

    # Final summary
    store.save_summary()
    tokens = get_token_usage()

    print(f"\n{'='*50}")
    print(f"Search complete!")
    print(f"  Total experiments: {store.total_count()}")
    print(f"  Good strategies: {store.good_count()}")
    print(f"  Directions explored: {len(store._directions)}")
    if tokens["calls"] > 0:
        print(f"  LLM calls: {tokens['calls']}")
        print(f"  Tokens: {tokens['input']} in, {tokens['output']} out")

    best = store.get_best()
    if best:
        print(f"\n  Best strategy:")
        print(f"    Quality score: {float(best.get('quality_score', 0)):.3f}")
        print(f"    Sharpe: {float(best.get('sharpe', 0)):.2f}")
        print(f"    Max drawdown: {float(best.get('max_drawdown', 0)):.2%}")
        print(f"    Params: {best.get('params_json', '')[:300]}")


if __name__ == "__main__":
    main()
