#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import redis

# Ensure repo root is on sys.path when running as a script.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Watch live market feature snapshots from Redis stream md.features.1m")
    p.add_argument("--redis-url", default=os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"))
    p.add_argument("--stream", default="md.features.1m")
    p.add_argument("--symbols", default="", help="Comma separated symbols to display, default uses payload universe")
    p.add_argument("--from-start", action="store_true", help="Read from oldest message instead of only new messages")
    p.add_argument("--show-json", action="store_true", help="Print raw payload JSON")
    p.add_argument("--block-ms", type=int, default=70000, help="XREAD block timeout in ms")
    return p.parse_args()


def format_num(v: Any, nd: int = 6) -> str:
    try:
        return f"{float(v):.{nd}f}"
    except Exception:
        return "-"


def format_row(sym: str, data: Dict[str, Any]) -> str:
    mid = format_num(data.get("mid_px", {}).get(sym), 6)
    r1 = format_num(data.get("ret_1m", {}).get(sym), 6)
    r5 = format_num(data.get("ret_5m", {}).get(sym), 6)
    r60 = format_num(data.get("ret_1h", {}).get(sym), 6)
    vol = format_num(data.get("vol_1h", {}).get(sym), 6)
    return f"{sym:>6}  mid={mid:>12}  ret1m={r1:>10}  ret5m={r5:>10}  ret1h={r60:>10}  vol1h={vol:>10}"


def decode_payload(fields: Dict[str, Any]) -> Dict[str, Any]:
    raw = fields.get("p")
    if not isinstance(raw, str):
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def main() -> int:
    args = parse_args()
    r = redis.Redis.from_url(args.redis_url, decode_responses=True)

    symbols_filter: List[str] = []
    if args.symbols.strip():
        symbols_filter = [s.strip() for s in args.symbols.split(",") if s.strip()]

    last_id = "0-0" if args.from_start else "$"
    stream = args.stream

    print(f"watching stream={stream} redis={args.redis_url} from_id={last_id}")
    while True:
        try:
            res = r.xread({stream: last_id}, block=args.block_ms, count=10)  # type: ignore[arg-type]
            if not res:
                continue
            for _, msgs in res:
                for msg_id, fields in msgs:
                    last_id = msg_id
                    payload = decode_payload(fields)
                    env = payload.get("env", {}) if isinstance(payload, dict) else {}
                    data = payload.get("data", {}) if isinstance(payload, dict) else {}
                    if not isinstance(data, dict):
                        print(f"{msg_id} invalid payload")
                        continue

                    ts = env.get("ts", "-") if isinstance(env, dict) else "-"
                    cycle = env.get("cycle_id", "-") if isinstance(env, dict) else "-"
                    asof = data.get("asof_minute", "-")
                    universe = data.get("universe", [])
                    if not isinstance(universe, list):
                        universe = []
                    symbols = symbols_filter if symbols_filter else universe

                    print("")
                    print(f"[{ts}] id={msg_id} cycle={cycle} asof={asof}")
                    for sym in symbols:
                        print(format_row(sym, data))

                    if args.show_json:
                        print(json.dumps(payload, ensure_ascii=False))
        except KeyboardInterrupt:
            print("\nstop")
            return 0
        except Exception as e:
            print(f"watch error: {e}")
            time.sleep(1.0)


if __name__ == "__main__":
    raise SystemExit(main())
