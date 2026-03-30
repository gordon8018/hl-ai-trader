"""CLI: LLM-driven parameter optimization search."""
from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="LLM-driven parameter search")
    parser.add_argument("--num-workers", type=int, default=2)
    parser.add_argument("--max-rounds", type=int, default=10)
    parser.add_argument("--data-dir", default="research/data/parquet")
    parser.add_argument("--output-dir", default="research/reports/optimizer")
    parser.add_argument("--holdout", help="Holdout date range for OOS validation")
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    from research.optimizer.search_space import SEARCH_DIMENSIONS, GOOD_THRESHOLDS
    from research.optimizer.results_store import ResultsStore
    from research.optimizer.param_generator import ParamGenerator
    from research.optimizer.executor import ExperimentExecutor
    from research.optimizer.evaluator import CryptoEvaluator

    store = ResultsStore(output_dir=output_dir)
    generator = ParamGenerator(search_dims=SEARCH_DIMENSIONS)
    executor = ExperimentExecutor(data_dir=args.data_dir, num_workers=args.num_workers)
    evaluator = CryptoEvaluator(thresholds=GOOD_THRESHOLDS)

    print(f"Starting parameter search with {args.num_workers} workers, max {args.max_rounds} rounds")

    for round_num in range(1, args.max_rounds + 1):
        print(f"\n--- Round {round_num}/{args.max_rounds} ---")
        params_batch = generator.generate(store.get_history())
        results = executor.run_batch(params_batch)
        for params, result in zip(params_batch, results):
            evaluation = evaluator.evaluate(result)
            store.add(params, evaluation)
            if evaluation.get("is_good"):
                print(f"  Good strategy found: sharpe={evaluation.get('sharpe', 0):.2f}")
        if store.is_saturated():
            print("Search saturated")
            break

    store.save_summary()
    print(f"\nSearch complete. Results in {output_dir}/")
    print(f"  Total experiments: {store.total_count()}")
    print(f"  Good strategies: {store.good_count()}")

    best = store.get_best()
    if best:
        print(f"  Best quality score: {best.get('quality_score', 0):.3f}")


if __name__ == "__main__":
    main()
