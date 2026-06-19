from __future__ import annotations

import argparse
import json
from typing import Any, Optional

from shared.exchange.factory import create_exchange_adapter


def run_probe(
    *,
    adapter,
    symbols: list[str],
    place_order: bool,
    symbol: Optional[str] = None,
    side: str = "buy",
    qty: float = 0.0,
    limit_px: float = 0.0,
    tif: str = "GTC",
    reduce_only: bool = False,
    client_order_id: Optional[str] = None,
    cancel_after: bool = False,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "market": adapter.get_all_mids(symbols),
    }
    account = adapter.get_account_state()
    out["account"] = {
        "equity_usd": getattr(account, "equity_usd", 0.0),
        "cash_usd": getattr(account, "cash_usd", 0.0),
        "positions": len(getattr(account, "positions", {}) or {}),
        "open_orders": len(getattr(account, "open_orders", []) or []),
    }
    out["instrument_specs"] = {}
    for sym in symbols:
        spec = adapter.get_instrument_spec(sym)
        out["instrument_specs"][sym] = {
            "min_qty": getattr(spec, "min_qty", None),
            "qty_step": getattr(spec, "qty_step", None),
            "price_tick": getattr(spec, "price_tick", None),
        }
    if not place_order:
        return out

    if not symbol or qty <= 0 or limit_px <= 0:
        raise ValueError("place_order=true requires symbol, qty, and limit_px")

    res = adapter.place_limit(
        symbol,
        side.lower() == "buy",
        qty,
        limit_px,
        tif=tif,
        reduce_only=reduce_only,
        client_order_id=client_order_id,
    )
    out["order"] = {
        "status": getattr(res, "status", None),
        "exchange_order_id": getattr(res, "exchange_order_id", None),
        "filled_qty": getattr(res, "filled_qty", None),
        "avg_px": getattr(res, "avg_px", None),
    }
    if cancel_after and getattr(res, "exchange_order_id", None):
        out["cancelled"] = adapter.cancel_order(symbol, str(res.exchange_order_id))
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Hyperliquid realtime/live probe using existing project config")
    p.add_argument("--symbols", default="BTC,ETH,SOL")
    p.add_argument("--place-order", action="store_true", help="place a test limit order using current env config")
    p.add_argument("--symbol", default=None)
    p.add_argument("--side", choices=["buy", "sell"], default="buy")
    p.add_argument("--qty", type=float, default=0.0)
    p.add_argument("--limit-px", type=float, default=0.0)
    p.add_argument("--tif", default="GTC")
    p.add_argument("--reduce-only", action="store_true")
    p.add_argument("--client-order-id", default=None)
    p.add_argument("--cancel-after", action="store_true", help="cancel the test order after placement")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    adapter = create_exchange_adapter()
    payload = run_probe(
        adapter=adapter,
        symbols=[s.strip() for s in args.symbols.split(",") if s.strip()],
        place_order=args.place_order,
        symbol=args.symbol,
        side=args.side,
        qty=args.qty,
        limit_px=args.limit_px,
        tif=args.tif,
        reduce_only=args.reduce_only,
        client_order_id=args.client_order_id,
        cancel_after=args.cancel_after,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
