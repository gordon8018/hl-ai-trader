# backtester/evaluator.py
"""
Evaluate a list of Prediction objects and print a scoring report.
"""
from __future__ import annotations
from collections import defaultdict
from typing import List

from backtester.replay import Prediction


def evaluate(predictions: List[Prediction], fee_per_trade_usd: float = 0.006) -> dict:
    """
    Compute win rate, avg gain/loss, PnL estimate per market_state.
    Returns structured metrics dict.
    """
    if not predictions:
        return {"error": "no predictions"}

    groups = defaultdict(list)
    groups["ALL"] = predictions
    for p in predictions:
        groups[p.market_state].append(p)
        groups[p.symbol].append(p)

    results = {}
    for label, preds in sorted(groups.items()):
        active = [p for p in preds if p.direction != "HOLD"]
        if not active:
            continue
        wins = [p for p in active if p.won]
        losses = [p for p in active if not p.won]
        win_rate = len(wins) / len(active)
        # signed_ret: positive = profit for the given direction (SHORT profit = price fell)
        def signed_ret(p):
            return p.ret if p.direction == "LONG" else -p.ret
        avg_win = sum(signed_ret(p) for p in wins) / len(wins) if wins else 0.0
        avg_loss = sum(signed_ret(p) for p in losses) / len(losses) if losses else 0.0
        rr = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")
        breakeven_wr = 1 / (1 + rr) if rr != float("inf") else 0.5
        # Estimate PnL assuming $100 position per trade, minus fees
        est_pnl_per_trade = win_rate * avg_win * 100 + (1 - win_rate) * avg_loss * 100
        net_pnl_per_trade = est_pnl_per_trade - fee_per_trade_usd
        results[label] = {
            "n": len(active),
            "win_rate": win_rate,
            "avg_win_pct": avg_win * 100,
            "avg_loss_pct": avg_loss * 100,
            "reward_risk": rr,
            "breakeven_win_rate": breakeven_wr,
            "est_net_pnl_per_trade_usd": net_pnl_per_trade,
            "wins": len(wins),
            "losses": len(losses),
        }
    return results


def print_report(results: dict) -> None:
    """Pretty-print evaluation results."""
    print("\n" + "=" * 80)
    print("BACKTEST EVALUATION REPORT")
    print("=" * 80)

    all_res = results.get("ALL")
    if all_res:
        wr = all_res["win_rate"] * 100
        be = all_res["breakeven_win_rate"] * 100
        net = all_res["est_net_pnl_per_trade_usd"]
        verdict = "PROFITABLE" if wr > be else "LOSING"
        print(f"\nOVERALL: {verdict}")
        print(f"  Win rate: {wr:.1f}%  (need >{be:.1f}% to break even)")
        print(f"  Reward/Risk: {all_res['reward_risk']:.2f}")
        print(f"  Est net PnL per $100 trade: ${net:.3f}")
        print(f"  Total predictions: {all_res['n']}")

    print("\nBY MARKET STATE:")
    for label in ("TRENDING", "SIDEWAYS", "VOLATILE", "EMERGENCY"):
        if label in results:
            r = results[label]
            print(f"  {label:<12}: n={r['n']:>4}  WR={r['win_rate']*100:>5.1f}%  "
                  f"RR={r['reward_risk']:>4.2f}  net=${r['est_net_pnl_per_trade_usd']:>+.3f}/trade")

    print("\nBY SYMBOL:")
    for sym in ("BTC", "ETH", "SOL", "ADA", "DOGE"):
        if sym in results:
            r = results[sym]
            print(f"  {sym:<6}: n={r['n']:>4}  WR={r['win_rate']*100:>5.1f}%  "
                  f"net=${r['est_net_pnl_per_trade_usd']:>+.3f}/trade")

    print("=" * 80)
    if all_res:
        wr = all_res["win_rate"] * 100
        be = all_res["breakeven_win_rate"] * 100
        if wr >= be:
            print("RECOMMENDATION: Strategy appears profitable. Consider deploying to live.")
        else:
            gap = be - wr
            print(f"RECOMMENDATION: Win rate gap is {gap:.1f}%. Tune prompt before deploying.")
    print()
