# Trading Non-Execution Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复“有持仓却不交易/不平仓”和 `ctl.error` 重试风暴，恢复系统可交易性与可观测性。

**Architecture:** 在 `ai_decision` 将重平衡判定从“candidate vs prev target”切换为“candidate vs current position”，并增加仓位一致性保护与审计。 在 `execution` 为 `ctl.commands` 增加消息规范化层（兼容 `p` 包装）以及不可恢复错误 ACK+DLQ 策略，防止 pending 死循环。 最后补齐指标、测试与配置档位，按分阶段发布。

**Tech Stack:** Python 3.12, Pydantic v2, Redis Streams, Prometheus metrics, pytest

---

## File Structure

- Modify: `services/ai_decision/app.py`
  - 职责：重平衡判定、目标/仓位一致性保护、审计事件输出
- Modify: `services/execution/app.py`
  - 职责：`ctl.commands` 消息解析、错误分类、ACK/DLQ 策略
- Modify: `shared/metrics/prom.py`
  - 职责：新增核心观测指标定义
- Modify: `config/trading_params.json`
  - 职责：新增 `V9_safe / V9_recovery / V9_prod` 运行档
- Create: `tests/test_ai_rebalance_alignment.py`
  - 职责：覆盖“candidate/prev/current”重平衡行为
- Create: `tests/test_execution_ctl_commands.py`
  - 职责：覆盖 `p` 包装、非法消息 ACK、防重试
- Modify: `docs/superpowers/specs/2026-03-27-trading-nonexecution-stability-design.md`
  - 职责：必要时记录实现偏差（仅在实现与设计有差异时更新）

---

### Task 1: ai_decision 重平衡判定修复（以 current_w 为基准）

**Files:**
- Create: `tests/test_ai_rebalance_alignment.py`
- Modify: `services/ai_decision/app.py`

- [ ] **Step 1: 写失败测试（当前行为应失败）**

```python
# tests/test_ai_rebalance_alignment.py
from services.ai_decision import app


def test_should_rebalance_uses_current_weights_not_prev_weights(monkeypatch):
    # 配置阈值，保证 0.02 的仓位变化应该触发
    monkeypatch.setattr(app, "AI_SIGNAL_DELTA_THRESHOLD", 0.01)

    class DummyBus:
        def get_json(self, key):
            return None  # 不触发 min_major_interval

    bus = DummyBus()
    prev_w = {"BTC": 0.0, "ETH": 0.0}
    current_w = {"BTC": -0.02, "ETH": 0.01}
    candidate_w = {"BTC": 0.0, "ETH": 0.0}

    # 目标：基于 current_w 计算 delta，应触发 rebalance
    rebalance, delta, reason = app.should_rebalance(
        bus=bus,
        prev_w=prev_w,
        current_w=current_w,
        candidate_w=candidate_w,
        asof_minute="2026-03-27T03:05:00Z",
    )
    assert rebalance is True
    assert delta > 0.01
    assert reason == "major_rebalance"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_ai_rebalance_alignment.py::test_should_rebalance_uses_current_weights_not_prev_weights -v`
Expected: FAIL（`should_rebalance` 签名或行为不匹配）

- [ ] **Step 3: 最小实现修改 should_rebalance**

```python
# services/ai_decision/app.py
# before:
# def should_rebalance(bus, prev_w, candidate_w, asof_minute):
#     delta = sum(abs(candidate_w.get(sym, 0.0) - prev_w.get(sym, 0.0)) for sym in UNIVERSE)

# after:
def should_rebalance(
    bus: RedisStreams,
    prev_w: Dict[str, float],
    current_w: Dict[str, float],
    candidate_w: Dict[str, float],
    asof_minute: str,
) -> Tuple[bool, float, str]:
    delta = sum(
        abs(candidate_w.get(sym, 0.0) - current_w.get(sym, 0.0))
        for sym in UNIVERSE
    )
    if delta < AI_SIGNAL_DELTA_THRESHOLD:
        return False, delta, "signal_delta_below_threshold_vs_current"

    raw = bus.get_json(LATEST_MAJOR_TS_KEY)
    if raw and isinstance(raw, dict):
        last_ts = raw.get("ts")
        if isinstance(last_ts, (int, float)):
            now_dt = datetime.fromisoformat(asof_minute.replace("Z", "+00:00"))
            now_ts = now_dt.replace(second=0, microsecond=0).timestamp()
            if (now_ts - float(last_ts)) < (AI_MIN_MAJOR_INTERVAL_MIN * 60):
                return False, delta, "min_major_interval"

    return True, delta, "major_rebalance"
```

- [ ] **Step 4: 更新调用点并验证测试通过**

```python
# services/ai_decision/app.py main loop call-site
rebalance, signal_delta, action_reason = should_rebalance(
    bus=bus,
    prev_w=prev_w,
    current_w=current_w,
    candidate_w=candidate_w,
    asof_minute=fs.asof_minute,
)
```

Run: `pytest tests/test_ai_rebalance_alignment.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tests/test_ai_rebalance_alignment.py services/ai_decision/app.py
git commit -m "fix(ai_decision): rebalance against current positions instead of previous target"
```

---

### Task 2: ai_decision 增加仓位一致性保护与审计事件

**Files:**
- Modify: `services/ai_decision/app.py`
- Test: `tests/test_ai_rebalance_alignment.py`

- [ ] **Step 1: 写失败测试（目标/实际脱钩场景）**

```python
# tests/test_ai_rebalance_alignment.py

def test_position_target_mismatch_flag_when_current_nonzero_prev_zero():
    current_w = {"BTC": -0.02, "ETH": 0.01}
    prev_w = {"BTC": 0.0, "ETH": 0.0}

    mismatch, current_gross, prev_gross = app.detect_position_target_mismatch(
        current_w=current_w,
        prev_w=prev_w,
        epsilon=0.002,
    )

    assert mismatch is True
    assert current_gross > 0.0
    assert prev_gross == 0.0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_ai_rebalance_alignment.py::test_position_target_mismatch_flag_when_current_nonzero_prev_zero -v`
Expected: FAIL（函数不存在）

- [ ] **Step 3: 添加检测函数和审计输出**

```python
# services/ai_decision/app.py
POSITION_TARGET_MISMATCH_EPSILON = _get("POSITION_TARGET_MISMATCH_EPSILON", float, 0.002)


def detect_position_target_mismatch(
    current_w: Dict[str, float],
    prev_w: Dict[str, float],
    epsilon: float = POSITION_TARGET_MISMATCH_EPSILON,
) -> Tuple[bool, float, float]:
    current_gross = sum(abs(v) for v in current_w.values())
    prev_gross = sum(abs(v) for v in prev_w.values())
    mismatch = current_gross > epsilon and prev_gross <= epsilon
    return mismatch, current_gross, prev_gross
```

```python
# services/ai_decision/app.py main loop, after prev_w/current_w loaded
mismatch, current_gross, prev_gross = detect_position_target_mismatch(current_w, prev_w)
if mismatch:
    bus.xadd_json(
        AUDIT,
        require_env({
            "env": incoming_env.model_dump(),
            "event": "ai.position_target_mismatch",
            "data": {
                "asof_minute": fs.asof_minute,
                "current_gross": current_gross,
                "prev_target_gross": prev_gross,
                "candidate_gross": sum(abs(v) for v in candidate_w.values()) if 'candidate_w' in locals() else None,
            },
        }),
    )
```

- [ ] **Step 4: 运行测试通过并做模块回归**

Run:
- `pytest tests/test_ai_rebalance_alignment.py -v`
- `pytest tests/test_profitability_fix.py -v`

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add services/ai_decision/app.py tests/test_ai_rebalance_alignment.py
git commit -m "feat(ai_decision): add position-target mismatch guard and audit event"
```

---

### Task 3: execution 兼容 ctl `p` 包装并修复异常 ACK 策略

**Files:**
- Create: `tests/test_execution_ctl_commands.py`
- Modify: `services/execution/app.py`

- [ ] **Step 1: 写失败测试（`p` 包装消息可解析）**

```python
# tests/test_execution_ctl_commands.py
import json
from services.execution import app


def test_normalize_ctl_payload_accepts_wrapped_p_message():
    wrapped = {
        "p": json.dumps({
            "env": {
                "event_id": "e1",
                "ts": "2026-03-27T03:00:00Z",
                "source": "openclaw_ops",
                "cycle_id": "20260327T0300Z",
                "retry_count": 0,
                "schema_version": "1.0",
            },
            "data": {"cmd": "RESUME", "reason": "test"},
        })
    }
    normalized = app.normalize_ctl_payload(wrapped)
    assert normalized["data"]["cmd"] == "RESUME"
```

- [ ] **Step 2: 写失败测试（不可解析消息必须 ACK）**

```python
# tests/test_execution_ctl_commands.py

def test_normalize_ctl_payload_rejects_invalid_wrapped_message():
    wrapped = {"p": "{not-json"}
    try:
        app.normalize_ctl_payload(wrapped)
        raised = False
    except ValueError as e:
        raised = True
        assert "invalid_wrapped_payload" in str(e)
    assert raised is True
```

- [ ] **Step 3: 运行测试确认失败**

Run: `pytest tests/test_execution_ctl_commands.py -v`
Expected: FAIL（`normalize_ctl_payload` 不存在）

- [ ] **Step 4: 实现 normalize + 错误分类 + ACK**

```python
# services/execution/app.py

def normalize_ctl_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(payload, dict) and "p" in payload:
        raw = payload.get("p")
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except Exception as e:
                raise ValueError(f"invalid_wrapped_payload: {e}")
    if isinstance(payload, dict):
        return payload
    raise ValueError("invalid_payload_type")
```

```python
# services/execution/app.py inside consume_ctl_commands()
try:
    normalized = normalize_ctl_payload(payload)
    normalized = require_env(normalized)
    env = Envelope(**normalized["env"])
    cmd, reason = parse_ctl_command(normalized)
    if cmd is None:
        bus.xadd_json(AUDIT, require_env({
            "env": Envelope(source=SERVICE, cycle_id=env.cycle_id).model_dump(),
            "event": "ctl.invalid_command",
            "data": normalized.get("data"),
        }))
        bus.xack(STREAM_CTL, GROUP_CTL, msg_id)
        continue
    mode = mode_from_command(cmd)
    bus.set_json(
        CTL_MODE_KEY,
        {
            "mode": mode,
            "cmd": cmd,
            "reason": reason,
            "ts": env.ts,
            "source": SERVICE,
        },
        ex=7 * 24 * 3600,
    )
    bus.xack(STREAM_CTL, GROUP_CTL, msg_id)
except ValueError as e:
    env = Envelope(source=SERVICE, cycle_id=current_cycle_id())
    bus.xadd_json(AUDIT, require_env({
        "env": env.model_dump(),
        "event": "ctl.invalid_format",
        "err": str(e),
        "raw": payload,
    }))
    bus.xack(STREAM_CTL, GROUP_CTL, msg_id)
except Exception as e:
    env = Envelope(source=SERVICE, cycle_id=current_cycle_id())
    bus.xadd_json(AUDIT, require_env({
        "env": env.model_dump(),
        "event": "ctl.apply_error",
        "err": str(e),
        "raw": payload,
    }))
    # apply_error 可保留重试；首次版本建议也 ack + 写 dlq 避免风暴
    bus.xadd_json("dlq.ctl.commands", {"env": env.model_dump(), "reason": str(e), "data": payload})
    bus.xack(STREAM_CTL, GROUP_CTL, msg_id)
```

- [ ] **Step 5: 运行测试并提交**

Run: `pytest tests/test_execution_ctl_commands.py -v`
Expected: PASS

```bash
git add services/execution/app.py tests/test_execution_ctl_commands.py
git commit -m "fix(execution): normalize ctl payload and ack invalid messages to stop retry storm"
```

---

### Task 4: 增加关键观测指标

**Files:**
- Modify: `shared/metrics/prom.py`
- Modify: `services/ai_decision/app.py`
- Modify: `services/execution/app.py`

- [ ] **Step 1: 写失败测试（指标对象可导入）**

```python
# tests/test_execution_ctl_commands.py

def test_new_metrics_symbols_exist():
    from shared.metrics.prom import AI_TARGET_ACTUAL_GAP_GROSS, EXEC_CTL_PENDING_COUNT
    assert AI_TARGET_ACTUAL_GAP_GROSS is not None
    assert EXEC_CTL_PENDING_COUNT is not None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_execution_ctl_commands.py::test_new_metrics_symbols_exist -v`
Expected: FAIL（未定义）

- [ ] **Step 3: 定义并上报指标**

```python
# shared/metrics/prom.py
AI_TARGET_ACTUAL_GAP_GROSS = Gauge(
    "ai_target_actual_gap_gross",
    "Absolute gross gap between candidate target and current positions",
    ["service"],
)

EXEC_CTL_PENDING_COUNT = Gauge(
    "execution_ctl_pending_count",
    "Pending messages in ctl.commands consumer group",
    ["service"],
)
```

```python
# services/ai_decision/app.py (main loop)
target_actual_gap = sum(abs(candidate_w.get(sym, 0.0) - current_w.get(sym, 0.0)) for sym in UNIVERSE)
AI_TARGET_ACTUAL_GAP_GROSS.labels(SERVICE).set(target_actual_gap)
```

```python
# services/execution/app.py (periodic)
try:
    summary = bus.r.xpending(STREAM_CTL, GROUP_CTL)
    pending = int(summary.get("pending", 0)) if isinstance(summary, dict) else 0
    EXEC_CTL_PENDING_COUNT.labels(SERVICE).set(pending)
except Exception:
    pass
```

- [ ] **Step 4: 运行测试通过**

Run: `pytest tests/test_execution_ctl_commands.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add shared/metrics/prom.py services/ai_decision/app.py services/execution/app.py tests/test_execution_ctl_commands.py
git commit -m "feat(metrics): add target-actual gap and ctl pending gauges"
```

---

### Task 5: 参数分档与切换策略

**Files:**
- Modify: `config/trading_params.json`
- Test: `tests/test_config_loader.py`

- [ ] **Step 1: 写失败测试（可读取 recovery 档）**

```python
# tests/test_config_loader.py

def test_load_config_with_recovery_profile(tmp_path):
    import json
    from services.ai_decision.config_loader import load_config

    cfg = {
        "active_version": "V9_recovery",
        "versions": {
            "V9_recovery": {
                "REDIS_URL": "redis://127.0.0.1:6379/0",
                "AI_SIGNAL_DELTA_THRESHOLD": 0.05,
                "MIN_NOTIONAL_USD": 50.0,
            }
        },
    }
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")

    loaded = load_config(str(p))
    assert loaded["AI_SIGNAL_DELTA_THRESHOLD"] == 0.05
```

- [ ] **Step 2: 运行测试**

Run: `pytest tests/test_config_loader.py::test_load_config_with_recovery_profile -v`
Expected: PASS（先确保 loader 无需改动）

- [ ] **Step 3: 更新生产配置三档**

```json
{
  "active_version": "V9_prod",
  "versions": {
    "V9_safe": {
      "_description": "stability rollout safe profile",
      "UNIVERSE": "BTC,ETH",
      "REDIS_URL": "redis://127.0.0.1:6379/0",
      "AI_SIGNAL_DELTA_THRESHOLD": 0.08,
      "MIN_NOTIONAL_USD": 100.0,
      "MIN_TRADE_INTERVAL_MIN": 90,
      "MAX_TRADES_PER_DAY": 15
    },
    "V9_recovery": {
      "_description": "stability rollout recovery profile",
      "UNIVERSE": "BTC,ETH",
      "REDIS_URL": "redis://127.0.0.1:6379/0",
      "AI_SIGNAL_DELTA_THRESHOLD": 0.05,
      "MIN_NOTIONAL_USD": 50.0,
      "MIN_TRADE_INTERVAL_MIN": 90,
      "MAX_TRADES_PER_DAY": 15
    },
    "V9_prod": {
      "_description": "stability rollout production profile",
      "UNIVERSE": "BTC,ETH",
      "REDIS_URL": "redis://127.0.0.1:6379/0",
      "AI_SIGNAL_DELTA_THRESHOLD": 0.08,
      "MIN_NOTIONAL_USD": 100.0,
      "MIN_TRADE_INTERVAL_MIN": 90,
      "MAX_TRADES_PER_DAY": 15
    }
  }
}
```

- [ ] **Step 4: 验证 JSON 与加载**

Run:
- `.venv/bin/python -m json.tool config/trading_params.json >/dev/null`
- `pytest tests/test_config_loader.py -v`

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add config/trading_params.json tests/test_config_loader.py
git commit -m "chore(config): add safe recovery prod profiles for V9 rollout"
```

---

### Task 6: 端到端验收与运行手册补充

**Files:**
- Modify: `docs/runbook.md`
- Modify: `docs/superpowers/specs/2026-03-27-trading-nonexecution-stability-design.md` (only if needed)

- [ ] **Step 1: 增加验收命令与阈值**

```markdown
# docs/runbook.md 新增章节
## Non-execution stability checks
1. `ctl.error` 24h 降幅 >= 95%
2. `ctl.commands` pending 不持续上升
3. 当 current_w 非零且 target=0 时，出现 rebalance 与执行回报
```

- [ ] **Step 2: 执行验收命令并记录结果**

Run:
- `.venv/bin/python - <<'PY'
import sqlite3
conn=sqlite3.connect('data/reporting.db')
cur=conn.cursor()
cur.execute(\"select count(*) from audit_events where source='execution' and event='ctl.error' and ts >= datetime('now','-24 hours')\")
print('ctl.error_24h', cur.fetchone()[0])
cur.execute(\"select event,count(*) from audit_events where source='ai_decision' and ts >= datetime('now','-24 hours') group by event order by count(*) desc\")
print('ai_events_24h', cur.fetchall())
PY`
- `rg -n "Decision \\| action=" logs/local/ai_decision.log | tail -n 100`
- `.venv/bin/python - <<'PY'
import redis
r=redis.Redis.from_url('redis://127.0.0.1:6379/0', decode_responses=True)
print(r.xpending('ctl.commands','exec_ctl_grp'))
PY`

Expected:
- `ctl.error` 显著下降
- `HOLD` 不再长期单一原因垄断

- [ ] **Step 3: 提交文档**

```bash
git add docs/runbook.md docs/superpowers/specs/2026-03-27-trading-nonexecution-stability-design.md
git commit -m "docs(runbook): add non-execution stability acceptance checks"
```

---

## Self-Review Checklist

1. **Spec coverage:**
- Rebalance 基准修复：Task 1
- 一致性保护：Task 2
- ctl 解析与 ACK：Task 3
- 观测指标：Task 4
- 参数分档：Task 5
- 发布验收：Task 6

2. **Placeholder scan:**
- 无 `TBD/TODO/implement later`。
- 每个代码步骤都包含明确代码或命令。

3. **Type consistency:**
- `should_rebalance` 新签名在 Task 1 中定义，并在调用点同步更新。
- `normalize_ctl_payload` 在 Task 3 中定义并在同任务内消费。
