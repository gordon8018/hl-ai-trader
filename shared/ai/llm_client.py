from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import httpx


@dataclass
class LLMConfig:
    endpoint: str
    api_key: str
    model: str
    timeout_ms: int = 1500
    temperature: float = 0.1


def _extract_text_from_openai_like(resp: Dict[str, Any]) -> str:
    choices = resp.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    msg = first.get("message")
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""


def call_llm_json(
    cfg: LLMConfig,
    *,
    system_prompt: str,
    user_payload: Dict[str, Any],
) -> Tuple[Optional[str], Dict[str, Any], Optional[str]]:
    t0 = time.time()
    headers = {"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"}
    req_body = {
        "model": cfg.model,
        "temperature": cfg.temperature,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
    }
    meta: Dict[str, Any] = {"model": cfg.model}
    try:
        with httpx.Client(timeout=max(0.2, cfg.timeout_ms / 1000.0)) as client:
            resp = client.post(cfg.endpoint, headers=headers, json=req_body)
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
        text = _extract_text_from_openai_like(data)
        usage = data.get("usage", {}) if isinstance(data, dict) else {}
        if isinstance(usage, dict):
            meta["token_usage"] = usage
        meta["latency_ms"] = int((time.time() - t0) * 1000)
        return text or None, meta, None
    except Exception as e:
        meta["latency_ms"] = int((time.time() - t0) * 1000)
        return None, meta, str(e)
