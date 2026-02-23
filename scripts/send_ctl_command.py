#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure repo root is on sys.path when running as a script.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.bus.guard import require_env
from shared.bus.redis_streams import RedisStreams
from shared.ops_tools import build_ctl_message


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Send control command to ctl.commands stream.")
    p.add_argument("--cmd", required=True, choices=["HALT", "REDUCE_ONLY", "RESUME"], help="Control command")
    p.add_argument("--reason", default="", help="Operator reason")
    p.add_argument("--source", default="ops.manual", help="Envelope source")
    p.add_argument("--redis-url", default=os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"), help="Redis URL")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    bus = RedisStreams(args.redis_url)
    payload = build_ctl_message(args.cmd, reason=args.reason, source=args.source)
    msg_id = bus.xadd_json("ctl.commands", require_env(payload))
    print(f"sent ctl.commands id={msg_id} cmd={args.cmd} reason={args.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
