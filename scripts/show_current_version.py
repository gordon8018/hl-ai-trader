from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict


def format_version_payload(payload: Dict[str, Any]) -> str:
    return (
        f"version={payload.get('version', '')}\n"
        f"stage={payload.get('stage', '')}\n"
        f"promoted_at={payload.get('promoted_at', '')}\n"
        f"previous_version={payload.get('previous_version', '')}\n"
        f"reason={payload.get('reason', '')}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print deployment.current_version")
    parser.add_argument(
        "--payload",
        default="{}",
        help="JSON payload containing deployment.current_version fields",
    )
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.payload)
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON payload: {exc.msg}", file=sys.stderr)
        return 2
    if not isinstance(payload, dict):
        print("error: payload must be a JSON object", file=sys.stderr)
        return 2
    print(format_version_payload(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
