from __future__ import annotations

import argparse
import json
from typing import Any, Dict


def format_version_payload(payload: Dict[str, Any]) -> str:
    return (
        f"version={payload.get('version', '')}\n"
        f"stage={payload.get('stage', '')}\n"
        f"promoted_at={payload.get('promoted_at', '')}\n"
        f"previous_version={payload.get('previous_version', '')}\n"
        f"reason={payload.get('reason', '')}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Print deployment.current_version")
    parser.add_argument(
        "--payload",
        default="{}",
        help="JSON payload containing deployment.current_version fields",
    )
    args = parser.parse_args()
    payload = json.loads(args.payload)
    print(format_version_payload(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
