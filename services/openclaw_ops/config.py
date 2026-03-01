from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List


@dataclass
class OpsConfig:
    redis_url: str
    api_key: str
    allowed_write_streams: List[str]
    allowed_read_streams: List[str]
    idempotency_ttl: int


def _split_csv(value: str) -> List[str]:
    return [s.strip() for s in value.split(",") if s.strip()]


def load_config() -> OpsConfig:
    return OpsConfig(
        redis_url=os.environ["REDIS_URL"],
        api_key=os.environ.get("OPS_API_KEY", ""),
        allowed_write_streams=_split_csv(
            os.environ.get("OPS_ALLOWED_WRITE_STREAMS", "ctl.commands,audit.logs,ops.proposals")
        ),
        allowed_read_streams=_split_csv(
            os.environ.get("OPS_ALLOWED_READ_STREAMS", "audit.logs,exec.reports,state.snapshot")
        ),
        idempotency_ttl=int(os.environ.get("OPS_IDEMPOTENCY_TTL", "600")),
    )
