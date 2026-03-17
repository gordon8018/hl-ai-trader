# V9 Improvements Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将交易参数迁移至JSON版本化配置，应用V9参数调整，并实现三项代码级逻辑改进，目标将日净PnL从-0.7 USDC推向正盈利。

**Architecture:** 新增 `config/trading_params.json`（含V8/V9全量参数）和 `services/ai_decision/config_loader.py`（独立加载模块）；`app.py` 模块顶部调用 `load_config()` 替换所有 `os.environ.get()` 调用；三项逻辑改进均在 `app.py` 内修改对应函数。

**Tech Stack:** Python 3.11, pytest, json, Redis（已有）

---

## Chunk 1: JSON配置系统

### Task 1: 创建配置加载模块

**Files:**
- Create: `services/ai_decision/config_loader.py`
- Create: `tests/test_config_loader.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_config_loader.py
import json
import os
import pytest
import tempfile


def write_config(path, data):
    with open(path, "w") as f:
        json.dump(data, f)


def test_load_config_returns_active_version_params():
    from services.ai_decision.config_loader import load_config
    cfg = {
        "active_version": "V9",
        "versions": {
            "V9": {"MAX_GROSS": 0.30, "CAP_ALT": 0.08}
        }
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(cfg, f)
        path = f.name
    try:
        params = load_config(path)
        assert params["MAX_GROSS"] == 0.30
        assert params["CAP_ALT"] == 0.08
    finally:
        os.unlink(path)


def test_load_config_missing_version_raises():
    from services.ai_decision.config_loader import load_config
    cfg = {
        "active_version": "V99",
        "versions": {"V9": {"MAX_GROSS": 0.30}}
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(cfg, f)
        path = f.name
    try:
        with pytest.raises(KeyError, match="V99"):
            load_config(path)
    finally:
        os.unlink(path)


def test_load_config_missing_file_raises():
    from services.ai_decision.config_loader import load_config
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/path/config.json")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /home/gordonyang/workspace/myproject/hl-ai-trader
pytest tests/test_config_loader.py -v
```
预期：`ModuleNotFoundError: No module named 'services.ai_decision.config_loader'`

- [ ] **Step 3: 实现 config_loader.py**

```python
# services/ai_decision/config_loader.py
"""
JSON-based configuration loader for ai_decision service.
Replaces os.environ.get() parameter loading.

Usage:
    from services.ai_decision.config_loader import load_config
    params = load_config()   # reads config/trading_params.json by default
    MAX_GROSS = float(params["MAX_GROSS"])
"""
import json
import os
from typing import Any, Dict

# Default config path: config/trading_params.json relative to project root
_DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "config", "trading_params.json"
)


def load_config(path: str = None) -> Dict[str, Any]:
    """
    Load trading parameters from a JSON config file.

    Args:
        path: Path to the JSON config file. Defaults to config/trading_params.json.

    Returns:
        Dict of parameter name -> value for the active version.

    Raises:
        FileNotFoundError: If the config file does not exist.
        KeyError: If active_version is not found in versions.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    resolved = path or _DEFAULT_CONFIG_PATH
    if not os.path.exists(resolved):
        raise FileNotFoundError(f"Config file not found: {resolved}")

    with open(resolved, "r") as f:
        data = json.load(f)

    active = data["active_version"]
    if active not in data["versions"]:
        raise KeyError(f"active_version '{active}' not found in versions: {list(data['versions'].keys())}")

    return data["versions"][active]
```

- [ ] **Step 4: 运行测试确认通过**

```bash
pytest tests/test_config_loader.py -v
```
预期：3 passed

- [ ] **Step 5: 提交**

```bash
git add services/ai_decision/config_loader.py tests/test_config_loader.py
git commit -m "feat(config): add JSON-based config_loader for versioned trading parameters"
```

---

### Task 2: 创建 trading_params.json（含V8和V9参数）

**Files:**
- Create: `config/trading_params.json`

- [ ] **Step 1: 创建配置文件**

创建 `config/trading_params.json`，内容如下（注意 JSON 中布尔值用小写 `true`/`false`）：

```json
{
  "active_version": "V9",
  "versions": {
    "V8": {
      "_description": "2026-03-15上线版本，基于Mar10-15回测优化",
      "UNIVERSE": "BTC,ETH,SOL,ADA,DOGE",
      "ERROR_STREAK_THRESHOLD": 3,
      "STREAM_IN": "md.features.1m",
      "REDIS_URL": "redis://redis:6379/0",
      "CONSUMER": "ai_1",
      "MAX_RETRIES": 5,
      "MAX_GROSS": 0.35,
      "MAX_NET": 0.20,
      "CAP_BTC_ETH": 0.30,
      "CAP_ALT": 0.15,
      "AI_SMOOTH_ALPHA": 0.25,
      "AI_SMOOTH_ALPHA_HIGH": 0.60,
      "AI_SMOOTH_ALPHA_MID": 0.40,
      "AI_MIN_CONFIDENCE": 0.50,
      "AI_CONFIDENCE_HIGH_THRESHOLD": 0.75,
      "MAX_GROSS_HIGH": 0.50,
      "MAX_NET_HIGH": 0.35,
      "AI_TURNOVER_CAP": 0.05,
      "AI_TURNOVER_CAP_HIGH": 0.20,
      "AI_SIGNAL_DELTA_THRESHOLD": 0.15,
      "AI_MIN_MAJOR_INTERVAL_MIN": 30,
      "DIRECTION_REVERSAL_WINDOW_MIN": 30,
      "DIRECTION_REVERSAL_THRESHOLD": 2,
      "DIRECTION_REVERSAL_PENALTY": "zero",
      "COOLDOWN_MINUTES": 15,
      "RECENT_PNL_WINDOW": 5,
      "MAX_CONSECUTIVE_LOSS": 2,
      "PNL_DISABLE_DURATION_MIN": 60,
      "RECENT_LOSS_SCALE_FACTOR": 0.3,
      "RECENT_LOSS_THRESHOLD": 3.0,
      "DAILY_DRAWDOWN_HALT_USD": 3.0,
      "DAILY_DRAWDOWN_RESUME_HOURS": 4.0,
      "PORTFOLIO_LOSS_COUNTER_SHARED": true,
      "SCALE_BY_RECENT_LOSS": true,
      "FORCE_NET_DIRECTION": true,
      "MAX_NET_LONG_WHEN_DOWN": 0.0,
      "MAX_NET_SHORT_WHEN_UP": 0.0,
      "BEARISH_REGIME_LONG_BLOCK": true,
      "BEARISH_REGIME_RET1H_THRESHOLD": -0.005,
      "MAX_SLIPPAGE_EMERGENCY": 5,
      "PRICE_DROP_EMERGENCY_PCT": 2.0,
      "FORCE_CASH_WHEN_EXTREME": true,
      "VOL_REGIME_DEFENSIVE": 1,
      "TREND_AGREE_DEFENSIVE": true,
      "EXEC_DEFENSIVE_REJECT": 0.05,
      "EXEC_DEFENSIVE_LATENCY": 500,
      "EXEC_DEFENSIVE_SLIPPAGE": 8,
      "ORDER_CONSOLIDATE_PER_CYCLE": true,
      "MAX_ORDERS_PER_COIN_PER_CYCLE": 1,
      "POSITION_MAX_AGE_MIN": 30,
      "POSITION_PROFIT_TARGET_BPS": 15.0,
      "AI_DECISION_HORIZON": "30m",
      "AI_USE_LLM": false,
      "AI_LLM_MOCK_RESPONSE": "",
      "AI_LLM_ENDPOINT": "",
      "AI_LLM_API_KEY": "",
      "AI_LLM_MODEL": "",
      "AI_LLM_TIMEOUT_MS": 1500,
      "STREAM_IN_1H": "md.features.1h",
      "CONSUMER_1H": "ai_layer1_1",
      "MIN_NOTIONAL_USD": 50.0,
      "MAX_TRADES_PER_DAY": 30,
      "MAX_GROSS_TRENDING_HIGH": 0.65,
      "MAX_GROSS_TRENDING_MID": 0.45,
      "MAX_GROSS_SIDEWAYS": 0.00,
      "MAX_GROSS_VOLATILE": 0.20,
      "MAX_GROSS_HIGH_CONFIDENCE_THRESHOLD": 0.70
    },
    "V9": {
      "_description": "2026-03-17改进版，降换手+收紧ALT+提升盈亏比，目标日净PnL转正",
      "UNIVERSE": "BTC,ETH,SOL,ADA,DOGE",
      "ERROR_STREAK_THRESHOLD": 3,
      "STREAM_IN": "md.features.1m",
      "REDIS_URL": "redis://redis:6379/0",
      "CONSUMER": "ai_1",
      "MAX_RETRIES": 5,
      "MAX_GROSS": 0.30,
      "MAX_NET": 0.20,
      "CAP_BTC_ETH": 0.30,
      "CAP_ALT": 0.08,
      "AI_SMOOTH_ALPHA": 0.25,
      "AI_SMOOTH_ALPHA_HIGH": 0.60,
      "AI_SMOOTH_ALPHA_MID": 0.40,
      "AI_MIN_CONFIDENCE": 0.60,
      "AI_CONFIDENCE_HIGH_THRESHOLD": 0.75,
      "MAX_GROSS_HIGH": 0.50,
      "MAX_NET_HIGH": 0.35,
      "AI_TURNOVER_CAP": 0.03,
      "AI_TURNOVER_CAP_HIGH": 0.12,
      "AI_SIGNAL_DELTA_THRESHOLD": 0.20,
      "AI_MIN_MAJOR_INTERVAL_MIN": 45,
      "DIRECTION_REVERSAL_WINDOW_MIN": 30,
      "DIRECTION_REVERSAL_THRESHOLD": 2,
      "DIRECTION_REVERSAL_PENALTY": "zero",
      "COOLDOWN_MINUTES": 20,
      "RECENT_PNL_WINDOW": 5,
      "MAX_CONSECUTIVE_LOSS": 2,
      "PNL_DISABLE_DURATION_MIN": 60,
      "RECENT_LOSS_SCALE_FACTOR": 0.3,
      "RECENT_LOSS_THRESHOLD": 3.0,
      "DAILY_DRAWDOWN_HALT_USD": 2.0,
      "DAILY_DRAWDOWN_RESUME_HOURS": 4.0,
      "PORTFOLIO_LOSS_COUNTER_SHARED": true,
      "SCALE_BY_RECENT_LOSS": true,
      "FORCE_NET_DIRECTION": true,
      "MAX_NET_LONG_WHEN_DOWN": 0.0,
      "MAX_NET_SHORT_WHEN_UP": 0.0,
      "BEARISH_REGIME_LONG_BLOCK": true,
      "BEARISH_REGIME_RET1H_THRESHOLD": -0.005,
      "MAX_SLIPPAGE_EMERGENCY": 5,
      "PRICE_DROP_EMERGENCY_PCT": 2.0,
      "FORCE_CASH_WHEN_EXTREME": true,
      "VOL_REGIME_DEFENSIVE": 1,
      "TREND_AGREE_DEFENSIVE": true,
      "EXEC_DEFENSIVE_REJECT": 0.05,
      "EXEC_DEFENSIVE_LATENCY": 500,
      "EXEC_DEFENSIVE_SLIPPAGE": 8,
      "ORDER_CONSOLIDATE_PER_CYCLE": true,
      "MAX_ORDERS_PER_COIN_PER_CYCLE": 1,
      "POSITION_MAX_AGE_MIN": 30,
      "POSITION_PROFIT_TARGET_BPS": 25.0,
      "AI_DECISION_HORIZON": "30m",
      "AI_USE_LLM": false,
      "AI_LLM_MOCK_RESPONSE": "",
      "AI_LLM_ENDPOINT": "",
      "AI_LLM_API_KEY": "",
      "AI_LLM_MODEL": "",
      "AI_LLM_TIMEOUT_MS": 1500,
      "STREAM_IN_1H": "md.features.1h",
      "CONSUMER_1H": "ai_layer1_1",
      "MIN_NOTIONAL_USD": 50.0,
      "MAX_TRADES_PER_DAY": 30,
      "MAX_GROSS_TRENDING_HIGH": 0.65,
      "MAX_GROSS_TRENDING_MID": 0.45,
      "MAX_GROSS_SIDEWAYS": 0.00,
      "MAX_GROSS_VOLATILE": 0.20,
      "MAX_GROSS_HIGH_CONFIDENCE_THRESHOLD": 0.70
    }
  }
}
```

- [ ] **Step 2: 验证JSON格式有效**

```bash
python3 -c "import json; json.load(open('config/trading_params.json')); print('JSON valid')"
```
预期：`JSON valid`

- [ ] **Step 3: 提交**

```bash
git add config/trading_params.json
git commit -m "feat(config): add trading_params.json with V8 and V9 versioned parameters"
```

---

## Chunk 2: 替换 app.py 中的 os.environ.get 调用

### Task 3: 更新 app.py 使用 JSON 配置

**Files:**
- Modify: `services/ai_decision/app.py`（顶部参数块，约第53–252行）
- Modify: `tests/test_ai_decision_layer1.py`（更新fixture）
- Modify: `tests/test_ai_decision_layer2.py`（更新fixture）
- Modify: `tests/test_ai_decision_capital.py`（更新fixture）
- Modify: `tests/test_ai_decision_utils.py`（特殊处理：该文件使用不同加载模式）

- [ ] **Step 1: 在 app.py 顶部替换参数加载块**

**删除范围：** 第52–252行（从 `REDIS_URL = os.environ["REDIS_URL"]` 到 `MAX_GROSS_HIGH_CONFIDENCE_THRESHOLD = float(os.environ.get(...))` 结束）。

**注意1：** 第57–65行中有以下固定常量**不从环境变量读取**，在替换块中保留：
```python
STREAM_OUT = "alpha.target"
AUDIT = "audit.logs"
STATE_KEY = "latest.state.snapshot"
LATEST_ALPHA_KEY = "latest.alpha.target"
LATEST_MAJOR_TS_KEY = "latest.alpha.major.ts"
GROUP = "ai_grp"
```

**注意2：** 原第56行 `STREAM_IN = os.environ.get("AI_STREAM_IN", ...)` 的env key是 `AI_STREAM_IN`，JSON配置中改为 `STREAM_IN`（去掉前缀），两者不同，这是有意的改动。

**注意3：** 第231行的 `os.environ["SERVICE_NAME"] = SERVICE` 是一个**写操作**（不是读取参数），保留不动。

**注意4：** `REDIS_URL` 在原代码中是 `os.environ["REDIS_URL"]`（无默认值，缺失时硬报错）。替换后同样应在配置中缺失时硬报错，不能静默使用默认值，因为错误的Redis地址会导致生产事故。在 `_load_config` 之后立即验证：

```python
if "REDIS_URL" not in _cfg:
    raise KeyError("REDIS_URL must be specified in trading_params.json")
```

**替换后的参数块（完整）：**

```python
# ==================== 配置加载 ====================
import os as _os
from services.ai_decision.config_loader import load_config as _load_config

_CONFIG_PATH = _os.environ.get("AI_CONFIG_PATH")  # 仅供测试覆盖，生产不设置
_cfg = _load_config(_CONFIG_PATH)

# REDIS_URL 是必填项，缺失时立即报错（与原 os.environ["REDIS_URL"] 行为一致）
if "REDIS_URL" not in _cfg:
    raise KeyError("REDIS_URL must be specified in trading_params.json")


def _get(key, cast=str, default=None):
    """从JSON配置中读取参数。cast为bool时直接返回bool值，不经str转换。"""
    val = _cfg.get(key, default)
    if val is None:
        return default
    if cast is bool:
        # JSON中布尔值已是Python bool，直接返回
        if isinstance(val, bool):
            return val
        # 兼容字符串"true"/"false"
        return str(val).lower() == "true"
    return cast(val)


# ── 基础 ──────────────────────────────────────────────────────────────
UNIVERSE                        = _get("UNIVERSE", str, "BTC,ETH,SOL,ADA,DOGE").split(",")
ERROR_STREAK_THRESHOLD          = _get("ERROR_STREAK_THRESHOLD", int, 3)
STREAM_IN                       = _get("STREAM_IN", str, "md.features.1m")
REDIS_URL                       = _cfg["REDIS_URL"]  # 必填，不使用默认值
CONSUMER                        = _get("CONSUMER", str, "ai_1")
RETRY                           = RetryPolicy(max_retries=_get("MAX_RETRIES", int, 5))

# ── 敞口 ──────────────────────────────────────────────────────────────
MAX_GROSS                       = _get("MAX_GROSS", float, 0.35)
MAX_NET                         = _get("MAX_NET", float, 0.20)
CAP_BTC_ETH                     = _get("CAP_BTC_ETH", float, 0.30)
CAP_ALT                         = _get("CAP_ALT", float, 0.15)

# ── 平滑与置信度 ───────────────────────────────────────────────────────
AI_SMOOTH_ALPHA                 = _get("AI_SMOOTH_ALPHA", float, 0.25)
AI_SMOOTH_ALPHA_HIGH            = _get("AI_SMOOTH_ALPHA_HIGH", float, 0.60)
AI_SMOOTH_ALPHA_MID             = _get("AI_SMOOTH_ALPHA_MID", float, 0.40)
AI_MIN_CONFIDENCE               = _get("AI_MIN_CONFIDENCE", float, 0.50)
AI_CONFIDENCE_HIGH_THRESHOLD    = _get("AI_CONFIDENCE_HIGH_THRESHOLD", float, 0.75)
MAX_GROSS_HIGH                  = _get("MAX_GROSS_HIGH", float, 0.50)
MAX_NET_HIGH                    = _get("MAX_NET_HIGH", float, 0.35)

# ── 换手控制 ──────────────────────────────────────────────────────────
AI_TURNOVER_CAP                 = _get("AI_TURNOVER_CAP", float, 0.05)
AI_TURNOVER_CAP_HIGH            = _get("AI_TURNOVER_CAP_HIGH", float, 0.20)
AI_SIGNAL_DELTA_THRESHOLD       = _get("AI_SIGNAL_DELTA_THRESHOLD", float, 0.15)
AI_MIN_MAJOR_INTERVAL_MIN       = _get("AI_MIN_MAJOR_INTERVAL_MIN", int, 30)

# ── 方向反转 ──────────────────────────────────────────────────────────
DIRECTION_REVERSAL_WINDOW_MIN   = _get("DIRECTION_REVERSAL_WINDOW_MIN", int, 30)
DIRECTION_REVERSAL_THRESHOLD    = _get("DIRECTION_REVERSAL_THRESHOLD", int, 2)
DIRECTION_REVERSAL_PENALTY      = _get("DIRECTION_REVERSAL_PENALTY", str, "zero").lower()
COOLDOWN_MINUTES                = _get("COOLDOWN_MINUTES", int, 15)

# ── PnL 风控 ──────────────────────────────────────────────────────────
RECENT_PNL_WINDOW               = _get("RECENT_PNL_WINDOW", int, 5)
MAX_CONSECUTIVE_LOSS            = _get("MAX_CONSECUTIVE_LOSS", int, 2)
PNL_DISABLE_DURATION_MIN        = _get("PNL_DISABLE_DURATION_MIN", int, 60)
RECENT_LOSS_SCALE_FACTOR        = _get("RECENT_LOSS_SCALE_FACTOR", float, 0.3)
RECENT_LOSS_THRESHOLD           = _get("RECENT_LOSS_THRESHOLD", float, 3.0)

DAILY_DRAWDOWN_HALT_USD         = _get("DAILY_DRAWDOWN_HALT_USD", float, 3.0)
DAILY_DRAWDOWN_RESUME_HOURS     = _get("DAILY_DRAWDOWN_RESUME_HOURS", float, 4.0)
PORTFOLIO_LOSS_COUNTER_SHARED   = _get("PORTFOLIO_LOSS_COUNTER_SHARED", bool, True)
SCALE_BY_RECENT_LOSS            = _get("SCALE_BY_RECENT_LOSS", bool, True)

# ── 方向管控 ──────────────────────────────────────────────────────────
FORCE_NET_DIRECTION             = _get("FORCE_NET_DIRECTION", bool, True)
MAX_NET_LONG_WHEN_DOWN          = _get("MAX_NET_LONG_WHEN_DOWN", float, 0.0)
MAX_NET_SHORT_WHEN_UP           = _get("MAX_NET_SHORT_WHEN_UP", float, 0.0)
BEARISH_REGIME_LONG_BLOCK       = _get("BEARISH_REGIME_LONG_BLOCK", bool, True)
BEARISH_REGIME_RET1H_THRESHOLD  = _get("BEARISH_REGIME_RET1H_THRESHOLD", float, -0.005)

# ── 紧急减仓 ──────────────────────────────────────────────────────────
MAX_SLIPPAGE_EMERGENCY          = _get("MAX_SLIPPAGE_EMERGENCY", float, 5)
PRICE_DROP_EMERGENCY_PCT        = _get("PRICE_DROP_EMERGENCY_PCT", float, 2.0)
FORCE_CASH_WHEN_EXTREME         = _get("FORCE_CASH_WHEN_EXTREME", bool, True)

# ── 执行防御 ──────────────────────────────────────────────────────────
VOL_REGIME_DEFENSIVE            = _get("VOL_REGIME_DEFENSIVE", int, 1)
TREND_AGREE_DEFENSIVE           = _get("TREND_AGREE_DEFENSIVE", bool, True)
EXEC_DEFENSIVE_REJECT           = _get("EXEC_DEFENSIVE_REJECT", float, 0.05)
EXEC_DEFENSIVE_LATENCY          = _get("EXEC_DEFENSIVE_LATENCY", float, 500)
EXEC_DEFENSIVE_SLIPPAGE         = _get("EXEC_DEFENSIVE_SLIPPAGE", float, 8)

# ── 订单整合 ──────────────────────────────────────────────────────────
ORDER_CONSOLIDATE_PER_CYCLE     = _get("ORDER_CONSOLIDATE_PER_CYCLE", bool, True)
MAX_ORDERS_PER_COIN_PER_CYCLE   = _get("MAX_ORDERS_PER_COIN_PER_CYCLE", int, 1)

# ── 持仓管理 ──────────────────────────────────────────────────────────
POSITION_MAX_AGE_MIN            = _get("POSITION_MAX_AGE_MIN", int, 30)
POSITION_PROFIT_TARGET_BPS      = _get("POSITION_PROFIT_TARGET_BPS", float, 15.0)

# ── LLM 配置 ──────────────────────────────────────────────────────────
AI_DECISION_HORIZON             = _get("AI_DECISION_HORIZON", str, "30m")
AI_USE_LLM                      = _get("AI_USE_LLM", bool, False)
AI_LLM_MOCK_RESPONSE            = _get("AI_LLM_MOCK_RESPONSE", str, "")
AI_LLM_ENDPOINT                 = _get("AI_LLM_ENDPOINT", str, "").strip()
AI_LLM_API_KEY                  = _get("AI_LLM_API_KEY", str, "").strip()
AI_LLM_MODEL                    = _get("AI_LLM_MODEL", str, "").strip()
AI_LLM_TIMEOUT_MS               = _get("AI_LLM_TIMEOUT_MS", int, 1500)

# ── Layer1/2 流配置 ───────────────────────────────────────────────────
STREAM_IN_1H                    = _get("STREAM_IN_1H", str, "md.features.1h")
DIRECTION_BIAS_KEY              = "latest.direction_bias"
GROUP_1H                        = "ai_grp_1h"
CONSUMER_1H                     = _get("CONSUMER_1H", str, "ai_layer1_1")

# ── 资金利用率（mode-aware） ───────────────────────────────────────────
MIN_NOTIONAL_USD                = _get("MIN_NOTIONAL_USD", float, 50.0)
MAX_TRADES_PER_DAY              = _get("MAX_TRADES_PER_DAY", int, 30)

MAX_GROSS_TRENDING_HIGH         = _get("MAX_GROSS_TRENDING_HIGH", float, 0.65)
MAX_GROSS_TRENDING_MID          = _get("MAX_GROSS_TRENDING_MID", float, 0.45)
MAX_GROSS_SIDEWAYS              = _get("MAX_GROSS_SIDEWAYS", float, 0.00)
MAX_GROSS_VOLATILE              = _get("MAX_GROSS_VOLATILE", float, 0.20)
MAX_GROSS_HIGH_CONFIDENCE_THRESHOLD = _get("MAX_GROSS_HIGH_CONFIDENCE_THRESHOLD", float, 0.70)
```

- [ ] **Step 2: 更新测试 fixture**

以下 fixture 替换内容适用于4个测试文件：`test_ai_decision_layer1.py`、`test_ai_decision_layer2.py`、`test_ai_decision_capital.py`、`test_ai_decision_utils.py`。

每个文件顶部**添加** `import json`（若已有则跳过）。

将原有 `_clean_env` fixture 替换为（注意 Python 布尔值用大写 `True`/`False`，不是 JSON 的 `true`/`false`）：

```python
@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    cfg = {
        "active_version": "TEST",
        "versions": {
            "TEST": {
                "UNIVERSE": "BTC,ETH,SOL,ADA,DOGE",
                "REDIS_URL": "redis://localhost:6379/0",
                "MAX_GROSS": 0.35,
                "MAX_NET": 0.20,
                "CAP_BTC_ETH": 0.30,
                "CAP_ALT": 0.15,
                "AI_SMOOTH_ALPHA": 0.25,
                "AI_SMOOTH_ALPHA_HIGH": 0.60,
                "AI_SMOOTH_ALPHA_MID": 0.40,
                "AI_MIN_CONFIDENCE": 0.50,
                "AI_CONFIDENCE_HIGH_THRESHOLD": 0.75,
                "MAX_GROSS_HIGH": 0.50,
                "MAX_NET_HIGH": 0.35,
                "AI_TURNOVER_CAP": 0.05,
                "AI_TURNOVER_CAP_HIGH": 0.20,
                "AI_SIGNAL_DELTA_THRESHOLD": 0.15,
                "AI_MIN_MAJOR_INTERVAL_MIN": 30,
                "DIRECTION_REVERSAL_WINDOW_MIN": 30,
                "DIRECTION_REVERSAL_THRESHOLD": 2,
                "DIRECTION_REVERSAL_PENALTY": "zero",
                "COOLDOWN_MINUTES": 15,
                "RECENT_PNL_WINDOW": 5,
                "MAX_CONSECUTIVE_LOSS": 2,
                "PNL_DISABLE_DURATION_MIN": 60,
                "RECENT_LOSS_SCALE_FACTOR": 0.3,
                "RECENT_LOSS_THRESHOLD": 3.0,
                "DAILY_DRAWDOWN_HALT_USD": 3.0,
                "DAILY_DRAWDOWN_RESUME_HOURS": 4.0,
                "PORTFOLIO_LOSS_COUNTER_SHARED": True,
                "SCALE_BY_RECENT_LOSS": True,
                "FORCE_NET_DIRECTION": True,
                "MAX_NET_LONG_WHEN_DOWN": 0.0,
                "MAX_NET_SHORT_WHEN_UP": 0.0,
                "BEARISH_REGIME_LONG_BLOCK": True,
                "BEARISH_REGIME_RET1H_THRESHOLD": -0.005,
                "MAX_SLIPPAGE_EMERGENCY": 5,
                "PRICE_DROP_EMERGENCY_PCT": 2.0,
                "FORCE_CASH_WHEN_EXTREME": True,
                "VOL_REGIME_DEFENSIVE": 1,
                "TREND_AGREE_DEFENSIVE": True,
                "EXEC_DEFENSIVE_REJECT": 0.05,
                "EXEC_DEFENSIVE_LATENCY": 500,
                "EXEC_DEFENSIVE_SLIPPAGE": 8,
                "ORDER_CONSOLIDATE_PER_CYCLE": True,
                "MAX_ORDERS_PER_COIN_PER_CYCLE": 1,
                "POSITION_MAX_AGE_MIN": 30,
                "POSITION_PROFIT_TARGET_BPS": 15.0,
                "AI_DECISION_HORIZON": "30m",
                "AI_USE_LLM": False,
                "AI_LLM_MOCK_RESPONSE": "",
                "AI_LLM_ENDPOINT": "",
                "AI_LLM_API_KEY": "",
                "AI_LLM_MODEL": "",
                "AI_LLM_TIMEOUT_MS": 1500,
                "STREAM_IN": "md.features.1m",
                "STREAM_IN_1H": "md.features.1h",
                "CONSUMER": "ai_1",
                "CONSUMER_1H": "ai_layer1_1",
                "MAX_RETRIES": 5,
                "ERROR_STREAK_THRESHOLD": 3,
                "MIN_NOTIONAL_USD": 50.0,
                "MAX_TRADES_PER_DAY": 30,
                "MAX_GROSS_TRENDING_HIGH": 0.65,
                "MAX_GROSS_TRENDING_MID": 0.45,
                "MAX_GROSS_SIDEWAYS": 0.00,
                "MAX_GROSS_VOLATILE": 0.20,
                "MAX_GROSS_HIGH_CONFIDENCE_THRESHOLD": 0.70
            }
        }
    }
    import json
    config_file = tmp_path / "test_trading_params.json"
    config_file.write_text(json.dumps(cfg))
    monkeypatch.setenv("AI_CONFIG_PATH", str(config_file))
    yield
```

- [ ] **Step 2b: 特殊处理 test_ai_decision_utils.py**

该文件不使用 `@pytest.fixture` 而是在 `load_module()` 函数内直接调用 `os.environ.setdefault`。需替换 `load_module()` 为使用 `AI_CONFIG_PATH` 的版本：

**将原有的 `load_module()` 函数（第1–12行）替换为：**

```python
import importlib
import json
import os
import sys
import tempfile


def _make_test_config():
    """创建临时测试JSON配置文件，返回文件路径。"""
    cfg = {
        "active_version": "TEST",
        "versions": {
            "TEST": {
                "UNIVERSE": "BTC,ETH",
                "REDIS_URL": "redis://localhost:6379/0",
                "MAX_GROSS": 0.35, "MAX_NET": 0.20,
                "CAP_BTC_ETH": 0.30, "CAP_ALT": 0.15,
                "AI_SMOOTH_ALPHA": 0.25, "AI_SMOOTH_ALPHA_HIGH": 0.60,
                "AI_SMOOTH_ALPHA_MID": 0.40, "AI_MIN_CONFIDENCE": 0.50,
                "AI_CONFIDENCE_HIGH_THRESHOLD": 0.75, "MAX_GROSS_HIGH": 0.50,
                "MAX_NET_HIGH": 0.35, "AI_TURNOVER_CAP": 0.05,
                "AI_TURNOVER_CAP_HIGH": 0.20, "AI_SIGNAL_DELTA_THRESHOLD": 0.15,
                "AI_MIN_MAJOR_INTERVAL_MIN": 30, "DIRECTION_REVERSAL_WINDOW_MIN": 30,
                "DIRECTION_REVERSAL_THRESHOLD": 2, "DIRECTION_REVERSAL_PENALTY": "zero",
                "COOLDOWN_MINUTES": 15, "RECENT_PNL_WINDOW": 5,
                "MAX_CONSECUTIVE_LOSS": 2, "PNL_DISABLE_DURATION_MIN": 60,
                "RECENT_LOSS_SCALE_FACTOR": 0.3, "RECENT_LOSS_THRESHOLD": 3.0,
                "DAILY_DRAWDOWN_HALT_USD": 3.0, "DAILY_DRAWDOWN_RESUME_HOURS": 4.0,
                "PORTFOLIO_LOSS_COUNTER_SHARED": True, "SCALE_BY_RECENT_LOSS": True,
                "FORCE_NET_DIRECTION": True, "MAX_NET_LONG_WHEN_DOWN": 0.0,
                "MAX_NET_SHORT_WHEN_UP": 0.0, "BEARISH_REGIME_LONG_BLOCK": True,
                "BEARISH_REGIME_RET1H_THRESHOLD": -0.005, "MAX_SLIPPAGE_EMERGENCY": 5,
                "PRICE_DROP_EMERGENCY_PCT": 2.0, "FORCE_CASH_WHEN_EXTREME": True,
                "VOL_REGIME_DEFENSIVE": 1, "TREND_AGREE_DEFENSIVE": True,
                "EXEC_DEFENSIVE_REJECT": 0.05, "EXEC_DEFENSIVE_LATENCY": 500,
                "EXEC_DEFENSIVE_SLIPPAGE": 8, "ORDER_CONSOLIDATE_PER_CYCLE": True,
                "MAX_ORDERS_PER_COIN_PER_CYCLE": 1,
                "POSITION_MAX_AGE_MIN": 30, "POSITION_PROFIT_TARGET_BPS": 15.0,
                "AI_DECISION_HORIZON": "30m", "AI_USE_LLM": False,
                "AI_LLM_MOCK_RESPONSE": "", "AI_LLM_ENDPOINT": "",
                "AI_LLM_API_KEY": "", "AI_LLM_MODEL": "", "AI_LLM_TIMEOUT_MS": 1500,
                "STREAM_IN": "md.features.1m", "STREAM_IN_1H": "md.features.1h",
                "CONSUMER": "ai_1", "CONSUMER_1H": "ai_layer1_1",
                "MAX_RETRIES": 5, "ERROR_STREAK_THRESHOLD": 3,
                "MIN_NOTIONAL_USD": 50.0, "MAX_TRADES_PER_DAY": 30,
                "MAX_GROSS_TRENDING_HIGH": 0.65, "MAX_GROSS_TRENDING_MID": 0.45,
                "MAX_GROSS_SIDEWAYS": 0.00, "MAX_GROSS_VOLATILE": 0.20,
                "MAX_GROSS_HIGH_CONFIDENCE_THRESHOLD": 0.70
            }
        }
    }
    tf = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(cfg, tf)
    tf.close()
    return tf.name


def load_module():
    config_path = _make_test_config()
    os.environ["AI_CONFIG_PATH"] = config_path
    mod_name = "services.ai_decision.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    try:
        return importlib.import_module(mod_name)
    finally:
        os.unlink(config_path)
```

- [ ] **Step 3: 运行全套测试确认通过**

```bash
pytest tests/ -v --tb=short 2>&1 | tail -30
```
预期：原有所有测试通过（至少138 passed），test_config_loader.py 也通过。

- [ ] **Step 4: 确认 app.py 中无遗漏的 os.environ.get**

```bash
grep -n "os.environ.get" services/ai_decision/app.py
```
预期：**恰好1条结果**：`_CONFIG_PATH = _os.environ.get("AI_CONFIG_PATH")` 这一行在 `app.py` 顶部配置块中（测试用），其余不应再有任何 `os.environ.get`。

- [ ] **Step 5: 提交**

```bash
git add services/ai_decision/app.py \
    tests/test_ai_decision_layer1.py \
    tests/test_ai_decision_layer2.py \
    tests/test_ai_decision_capital.py \
    tests/test_ai_decision_utils.py
git commit -m "refactor(config): replace os.environ.get with JSON config loader in app.py"
```

---

## Chunk 3: 逻辑改进

### Task 4: 做空信号诊断日志

**Files:**
- Modify: `services/ai_decision/app.py`（`apply_direction_confirmation` 函数，约第1210–1258行）
- Modify: `tests/test_ai_decision_layer2.py`（新增诊断日志测试）

- [ ] **Step 1: 写失败测试**

在 `tests/test_ai_decision_layer2.py` 末尾追加：

```python
def test_direction_confirmation_logs_regime_info(caplog):
    import logging
    mod = load_ai()
    bias = make_bias({"BTC": "LONG"})
    fs = make_fs15()
    with caplog.at_level(logging.INFO, logger="services.ai_decision.app"):
        mod.apply_direction_confirmation(fs, bias, ["BTC"])
    log_text = " ".join(caplog.messages)
    # 应记录 market_state 和 active/blocked 信息
    assert "market_state" in log_text or "Layer2" in log_text


def test_direction_confirmation_logs_short_confirm(caplog):
    import logging
    mod = load_ai()
    bias = make_bias({"BTC": "SHORT"})
    fs = make_fs15({
        "funding_rate": {"BTC": -0.0001},
        "basis_bps": {"BTC": -5.0},
        "oi_change_15m": {"BTC": -100.0},
        "book_imbalance_l5": {"BTC": -0.15},
        "aggr_delta_5m": {"BTC": -500.0},
        "trend_strength_15m": {"BTC": 0.7},
    })
    with caplog.at_level(logging.DEBUG, logger="services.ai_decision.app"):
        result = mod.apply_direction_confirmation(fs, bias, ["BTC"])
    assert result["BTC"] < 0.0
    # debug日志中应包含 BTC 和 confirm 计数
    log_text = " ".join(caplog.messages)
    assert "BTC" in log_text
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/test_ai_decision_layer2.py::test_direction_confirmation_logs_regime_info \
       tests/test_ai_decision_layer2.py::test_direction_confirmation_logs_short_confirm -v
```
预期：FAIL（当前 `apply_direction_confirmation` 无INFO日志）

- [ ] **Step 3: 在 `apply_direction_confirmation` 中添加诊断日志**

当前函数体（第1210–1258行）完整如下，三处修改以注释标出：

```python
def apply_direction_confirmation(
    fs: FeatureSnapshot15m,
    bias: DirectionBias,
    universe: List[str],
    min_confirm: int = 3,
) -> Dict[str, float]:
    # 修改1：在 return 之前添加 logger.info
    if bias.market_state == "SIDEWAYS":
        logger.info("Layer2 decision | market_state=SIDEWAYS all_zero=True")   # ← 新增
        return {sym: 0.0 for sym in universe}

    bias_map = {b.symbol: b for b in bias.biases}
    result = {}

    for sym in universe:
        sym_bias = bias_map.get(sym)
        if not sym_bias or sym_bias.direction == "FLAT":
            result[sym] = 0.0
            continue
        # FLAT/无bias的symbol走 continue，不需要debug日志（confirm=N/A）

        direction = sym_bias.direction
        confirm = 0

        if direction == "LONG":
            if fs.funding_rate.get(sym, 0) > 0:             confirm += 1
            if fs.basis_bps.get(sym, 0) > 0:                confirm += 1
            if fs.oi_change_15m.get(sym, 0) > 0:            confirm += 1
            if fs.book_imbalance_l5.get(sym, 0) > 0.10:     confirm += 1
            if fs.aggr_delta_5m.get(sym, 0) > 0:            confirm += 1
            if fs.trend_strength_15m.get(sym, 0) > 0.6:     confirm += 1
        else:  # SHORT
            if fs.funding_rate.get(sym, 0) < 0:             confirm += 1
            if fs.basis_bps.get(sym, 0) < 0:                confirm += 1
            if fs.oi_change_15m.get(sym, 0) < 0:            confirm += 1
            if fs.book_imbalance_l5.get(sym, 0) < -0.10:    confirm += 1
            if fs.aggr_delta_5m.get(sym, 0) < 0:            confirm += 1
            if fs.trend_strength_15m.get(sym, 0) > 0.6:     confirm += 1

        if confirm >= min_confirm:
            cap = CAP_BTC_ETH if sym in ("BTC", "ETH") else CAP_ALT
            weight = cap * sym_bias.confidence
            result[sym] = weight if direction == "LONG" else -weight
        else:
            result[sym] = 0.0

        # 修改2：每个有方向的symbol循环末尾加 debug 日志（在 confirm 计算之后）
        logger.debug(                                                           # ← 新增
            "Layer2 | sym=%s dir=%s confirm=%d/%d weight=%.4f",               # ← 新增
            sym, direction, confirm, min_confirm, result[sym]                  # ← 新增
        )                                                                      # ← 新增

    # 修改3：return 之前添加 INFO 汇总
    active = {s: w for s, w in result.items() if w != 0.0}                    # ← 新增
    blocked = [                                                                # ← 新增
        s for s, w in result.items()                                           # ← 新增
        if w == 0.0 and bias_map.get(s) and bias_map[s].direction != "FLAT"   # ← 新增
    ]                                                                          # ← 新增
    logger.info(                                                               # ← 新增
        "Layer2 decision | market_state=%s active=%s blocked_by_confirm=%s",  # ← 新增
        bias.market_state, active, blocked                                     # ← 新增
    )                                                                          # ← 新增
    return result
```

- [ ] **Step 4: 运行测试确认通过**

```bash
pytest tests/test_ai_decision_layer2.py -v
```
预期：全部通过

- [ ] **Step 5: 提交**

```bash
git add services/ai_decision/app.py tests/test_ai_decision_layer2.py
git commit -m "feat(logging): add Layer2 direction confirmation diagnostic logs"
```

---

### Task 5: 持仓主动止盈（盈利目标触发平仓）

**Files:**
- Modify: `services/ai_decision/app.py`（新增 `apply_profit_target` 函数；在主循环约第1531–1537行之间接入）
- Create: `tests/test_ai_decision_profit_target.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ai_decision_profit_target.py
"""测试持仓主动止盈逻辑：当open持仓浮动PnL超过POSITION_PROFIT_TARGET_BPS时强制归零。"""
import json
import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    cfg = {
        "active_version": "TEST",
        "versions": {
            "TEST": {
                "UNIVERSE": "BTC,ETH,SOL,ADA,DOGE",
                "REDIS_URL": "redis://localhost:6379/0",
                "MAX_GROSS": 0.35, "MAX_NET": 0.20,
                "CAP_BTC_ETH": 0.30, "CAP_ALT": 0.15,
                "AI_SMOOTH_ALPHA": 0.25, "AI_SMOOTH_ALPHA_HIGH": 0.60,
                "AI_SMOOTH_ALPHA_MID": 0.40, "AI_MIN_CONFIDENCE": 0.50,
                "AI_CONFIDENCE_HIGH_THRESHOLD": 0.75, "MAX_GROSS_HIGH": 0.50,
                "MAX_NET_HIGH": 0.35, "AI_TURNOVER_CAP": 0.05,
                "AI_TURNOVER_CAP_HIGH": 0.20, "AI_SIGNAL_DELTA_THRESHOLD": 0.15,
                "AI_MIN_MAJOR_INTERVAL_MIN": 30, "DIRECTION_REVERSAL_WINDOW_MIN": 30,
                "DIRECTION_REVERSAL_THRESHOLD": 2, "DIRECTION_REVERSAL_PENALTY": "zero",
                "COOLDOWN_MINUTES": 15, "RECENT_PNL_WINDOW": 5,
                "MAX_CONSECUTIVE_LOSS": 2, "PNL_DISABLE_DURATION_MIN": 60,
                "RECENT_LOSS_SCALE_FACTOR": 0.3, "RECENT_LOSS_THRESHOLD": 3.0,
                "DAILY_DRAWDOWN_HALT_USD": 3.0, "DAILY_DRAWDOWN_RESUME_HOURS": 4.0,
                "PORTFOLIO_LOSS_COUNTER_SHARED": True, "SCALE_BY_RECENT_LOSS": True,
                "FORCE_NET_DIRECTION": True, "MAX_NET_LONG_WHEN_DOWN": 0.0,
                "MAX_NET_SHORT_WHEN_UP": 0.0, "BEARISH_REGIME_LONG_BLOCK": True,
                "BEARISH_REGIME_RET1H_THRESHOLD": -0.005, "MAX_SLIPPAGE_EMERGENCY": 5,
                "PRICE_DROP_EMERGENCY_PCT": 2.0, "FORCE_CASH_WHEN_EXTREME": True,
                "VOL_REGIME_DEFENSIVE": 1, "TREND_AGREE_DEFENSIVE": True,
                "EXEC_DEFENSIVE_REJECT": 0.05, "EXEC_DEFENSIVE_LATENCY": 500,
                "EXEC_DEFENSIVE_SLIPPAGE": 8, "ORDER_CONSOLIDATE_PER_CYCLE": True,
                "MAX_ORDERS_PER_COIN_PER_CYCLE": 1,
                "POSITION_MAX_AGE_MIN": 30,
                "POSITION_PROFIT_TARGET_BPS": 25.0,
                "AI_DECISION_HORIZON": "30m", "AI_USE_LLM": False,
                "AI_LLM_MOCK_RESPONSE": "", "AI_LLM_ENDPOINT": "",
                "AI_LLM_API_KEY": "", "AI_LLM_MODEL": "", "AI_LLM_TIMEOUT_MS": 1500,
                "STREAM_IN": "md.features.1m", "STREAM_IN_1H": "md.features.1h",
                "CONSUMER": "ai_1", "CONSUMER_1H": "ai_layer1_1",
                "MAX_RETRIES": 5, "ERROR_STREAK_THRESHOLD": 3,
                "MIN_NOTIONAL_USD": 50.0, "MAX_TRADES_PER_DAY": 30,
                "MAX_GROSS_TRENDING_HIGH": 0.65, "MAX_GROSS_TRENDING_MID": 0.45,
                "MAX_GROSS_SIDEWAYS": 0.00, "MAX_GROSS_VOLATILE": 0.20,
                "MAX_GROSS_HIGH_CONFIDENCE_THRESHOLD": 0.70
            }
        }
    }
    config_file = tmp_path / "test_trading_params.json"
    config_file.write_text(json.dumps(cfg))
    monkeypatch.setenv("AI_CONFIG_PATH", str(config_file))
    yield


def load_ai():
    import sys, importlib
    mod_name = "services.ai_decision.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def test_apply_profit_target_closes_profitable_position():
    """持仓盈利超过POSITION_PROFIT_TARGET_BPS时，apply_profit_target应将权重归零。"""
    mod = load_ai()
    current_weights = {"BTC": 0.20, "ETH": 0.0}
    position_pnl_bps = {"BTC": 30.0, "ETH": 0.0}  # BTC盈利30bps > 25bps目标
    result = mod.apply_profit_target(current_weights, position_pnl_bps)
    assert result["BTC"] == 0.0, "BTC盈利超目标，应被平仓（归零）"
    assert result["ETH"] == 0.0, "ETH无持仓，保持零"


def test_apply_profit_target_keeps_underperforming_position():
    """持仓盈利未达目标时，apply_profit_target不应改变权重。"""
    mod = load_ai()
    current_weights = {"BTC": 0.20}
    position_pnl_bps = {"BTC": 10.0}  # 10bps < 25bps目标
    result = mod.apply_profit_target(current_weights, position_pnl_bps)
    assert result["BTC"] == 0.20, "未达止盈目标，权重不变"


def test_apply_profit_target_ignores_zero_positions():
    """空仓位不受影响。"""
    mod = load_ai()
    current_weights = {"BTC": 0.0}
    position_pnl_bps = {"BTC": 100.0}
    result = mod.apply_profit_target(current_weights, position_pnl_bps)
    assert result["BTC"] == 0.0


def test_apply_profit_target_handles_short_position():
    """SHORT持仓（负权重）盈利超目标时也应归零。"""
    mod = load_ai()
    current_weights = {"BTC": -0.15}
    position_pnl_bps = {"BTC": 30.0}  # SHORT盈利30bps > 25bps
    result = mod.apply_profit_target(current_weights, position_pnl_bps)
    assert result["BTC"] == 0.0, "SHORT持仓盈利超目标，应归零"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/test_ai_decision_profit_target.py -v
```
预期：FAIL，`AttributeError: module has no attribute 'apply_profit_target'`

- [ ] **Step 3: 在 app.py 中实现 `apply_profit_target`**

在 `apply_direction_confirmation` 函数之后（约第1258行的 `return result` 和下方 `# === Capital helpers ===` 注释之间）新增：

```python
def apply_profit_target(
    current_weights: Dict[str, float],
    position_pnl_bps: Dict[str, float],
) -> Dict[str, float]:
    """
    主动止盈：当持仓浮动PnL（基点）>= POSITION_PROFIT_TARGET_BPS时将权重归零。

    该函数在主循环中于 apply_direction_confirmation 返回 target_w 后、
    turnover cap 之前调用，以便让止盈信号同样受 turnover cap 约束。

    Args:
        current_weights: 当前持仓权重 {symbol: weight}（非目标权重）
        position_pnl_bps: 各symbol当前浮动PnL基点 {symbol: pnl_bps}

    Returns:
        调整后的权重。触发止盈的symbol权重设为0.0，其余不变。
    """
    result = dict(current_weights)
    for sym, weight in current_weights.items():
        if weight == 0.0:
            continue
        pnl_bps = position_pnl_bps.get(sym, 0.0)
        if pnl_bps >= POSITION_PROFIT_TARGET_BPS:
            logger.info(
                "Profit target hit | sym=%s pnl_bps=%.1f target=%.1f weight %.4f -> 0.0",
                sym, pnl_bps, POSITION_PROFIT_TARGET_BPS, weight
            )
            result[sym] = 0.0
    return result
```

- [ ] **Step 4: 在主循环接入 `apply_profit_target`**

定位主循环中约第1537行的位置（在 `if daily_cap_reached:` 块之后，在 `prev_w, _ = get_prev_target(...)` 之前）。

原代码（含 `get_current_weights` 的调用）：

```python
                # Daily cap override (after weight building so emergency check below still runs)
                if daily_cap_reached:
                    target_w = {sym: 0.0 for sym in UNIVERSE}

                prev_w, _ = get_prev_target(bus, UNIVERSE)
                current_w = get_current_weights(bus, UNIVERSE)   # ← 这行之前插入
```

**将上述最后两行替换为以下三段**（合并止盈检查和原有的 `current_w` 赋值，避免重复调用 `get_current_weights`）：

```python
                prev_w, _ = get_prev_target(bus, UNIVERSE)
                current_w = get_current_weights(bus, UNIVERSE)  # 原有行，保留

                # 主动止盈检查：复用上方已获取的 current_w，不重复读Redis
                # position_pnl_bps 字段若 FeatureSnapshot15m 暂不提供则安全跳过
                if hasattr(fs, "position_pnl_bps") and fs.position_pnl_bps:
                    profit_adjusted_w = apply_profit_target(
                        current_w, fs.position_pnl_bps
                    )
                    # 若止盈触发（current_w 中非零 → profit_adjusted_w 归零）
                    # 则同步将 target_w 中对应 symbol 归零，触发平仓订单
                    for sym in UNIVERSE:
                        if profit_adjusted_w.get(sym, 0.0) == 0.0 and \
                                current_w.get(sym, 0.0) != 0.0:
                            target_w[sym] = 0.0
```

> **注意：** `time` 模块已在 `app.py` 顶部 import，无需新增。

- [ ] **Step 5: 运行全套测试**

```bash
pytest tests/ -v --tb=short 2>&1 | tail -20
```
预期：全部通过

- [ ] **Step 6: 提交**

```bash
git add services/ai_decision/app.py tests/test_ai_decision_profit_target.py
git commit -m "feat(risk): add apply_profit_target() for active take-profit at 25bps (V9)"
```

---

### Task 6: Layer1 触发频率保护（去抖动）

**Files:**
- Modify: `services/ai_decision/app.py`（新增常量；修改 `process_1h_message` 函数，约第1166–1207行）
- Modify: `tests/test_ai_decision_layer1.py`（新增去抖动测试）

- [ ] **Step 1: 添加 Layer1 去抖动常量**

在 `app.py` 中 `DAILY_TRADE_CAP_KEY_PREFIX` 附近（约第1311行）添加：

```python
LAYER1_LAST_DECISION_TS_KEY = "layer1_last_decision_ts"
LAYER1_DEBOUNCE_MIN = 55  # 55分钟内不重复触发Layer1（防止连锁高频）
```

- [ ] **Step 2: 写失败测试**

在 `tests/test_ai_decision_layer1.py` 末尾追加：

```python
def test_process_1h_message_debounce_skips_when_too_soon():
    """若距上次Layer1决策不足55分钟，process_1h_message应直接返回None。"""
    import time
    mod = load_ai()
    from tests.fake_redis import FakeRedis
    from shared.schemas import FeatureSnapshot1h

    fake_r = FakeRedis()
    # 模拟上次决策在30分钟前（< 55分钟去抖动阈值）
    fake_r.set(mod.LAYER1_LAST_DECISION_TS_KEY, str(time.time() - 30 * 60))

    class FakeBus:
        r = fake_r
        def xadd_json(self, *a, **kw): pass

    fs1h = FeatureSnapshot1h(
        asof_minute="2026-03-17T10:00:00Z",
        universe=["BTC"],
        mid_px={"BTC": 70000.0},
        ret_1h={"BTC": 0.005},
        ret_4h={"BTC": 0.01},
        trend_1h={"BTC": 1},
        trend_4h={"BTC": 1},
        trend_agree={"BTC": 1},
        vol_regime={"BTC": 0},
        funding_rate={"BTC": 0.0001},
    )
    result = mod.process_1h_message(fs1h, FakeBus(), None)
    assert result is None, "距上次决策不足55分钟，应返回None（debounce）"


def test_process_1h_message_debounce_passes_when_no_prior_decision():
    """首次运行（Redis中无LAYER1_LAST_DECISION_TS_KEY记录）时不被debounce阻止。
    验证方式：检查 FakeRedis 在函数返回后没有写入 debounce key（因为 AI_USE_LLM=False
    且 mock response 为空，函数在 parse 阶段就返回了 None，但在此之前已通过 debounce 检查）。
    更精确地说：若被 debounce 阻止，FakeRedis 中的 LAYER1_LAST_DECISION_TS_KEY
    不会被写入（因为 debounce 在写入时间戳之前就 return None）。
    若通过 debounce，函数会尝试调用 build_1h_user_payload 并推进——
    我们用 mock 来观察是否推进到了 debounce 检查之后。
    """
    import time
    mod = load_ai()
    from tests.fake_redis import FakeRedis
    from shared.schemas import FeatureSnapshot1h

    fake_r = FakeRedis()
    # 不设置 LAYER1_LAST_DECISION_TS_KEY，模拟首次运行
    assert fake_r.get(mod.LAYER1_LAST_DECISION_TS_KEY) is None

    call_log = []

    class FakeBus:
        r = fake_r
        def xadd_json(self, *a, **kw): pass

    # Monkey-patch build_1h_user_payload 来检测函数是否越过了 debounce 检查
    original_build = mod.build_1h_user_payload
    def patched_build(fs1h, universe):
        call_log.append("build_called")
        return original_build(fs1h, universe)
    mod.build_1h_user_payload = patched_build

    fs1h = FeatureSnapshot1h(
        asof_minute="2026-03-17T10:00:00Z",
        universe=["BTC"],
        mid_px={"BTC": 70000.0},
        ret_1h={"BTC": 0.005},
        ret_4h={"BTC": 0.01},
        trend_1h={"BTC": 1},
        trend_4h={"BTC": 1},
        trend_agree={"BTC": 1},
        vol_regime={"BTC": 0},
        funding_rate={"BTC": 0.0001},
    )
    try:
        mod.process_1h_message(fs1h, FakeBus(), None)
    finally:
        mod.build_1h_user_payload = original_build  # 恢复

    # 首次运行（无记录）不应被 debounce 阻止，build_1h_user_payload 必须被调用
    assert "build_called" in call_log, "首次运行未被debounce阻止，应调用到build_1h_user_payload"
```

- [ ] **Step 3: 运行测试确认第一个失败**

```bash
pytest tests/test_ai_decision_layer1.py::test_process_1h_message_debounce_skips_when_too_soon -v
```
预期：FAIL（当前无debounce逻辑，函数会继续执行而不返回None）

- [ ] **Step 4: 在 `process_1h_message` 中实现去抖动**

当前函数（第1166行）开头：

```python
def process_1h_message(fs1h, bus, llm_cfg):
    """Layer 1: ..."""
    user_payload = build_1h_user_payload(fs1h, UNIVERSE)   # ← 在这行之前插入去抖动
```

在 `user_payload = build_1h_user_payload(...)` 之前插入：

```python
    # Layer1 去抖动：55分钟内不重复触发，防止连锁高频调仓（参见3/15事故）
    last_ts_raw = bus.r.get(LAYER1_LAST_DECISION_TS_KEY)
    if last_ts_raw:
        try:
            elapsed_min = (time.time() - float(last_ts_raw)) / 60.0
            if elapsed_min < LAYER1_DEBOUNCE_MIN:
                logger.debug(
                    "Layer1 debounce: skip (last=%.1f min ago, threshold=%d min)",
                    elapsed_min, LAYER1_DEBOUNCE_MIN,
                )
                return None
        except (ValueError, TypeError):
            pass  # 无效时间戳，继续正常执行
```

在函数末尾，`store_direction_bias(bus, bias)` 之后、`logger.info(...)` 之前插入时间戳记录：

```python
    store_direction_bias(bus, bias)
    bus.r.set(LAYER1_LAST_DECISION_TS_KEY, str(time.time()))
    logger.info(
        "Layer 1 decision | market_state=%s | biases=%s",
        bias.market_state,
        [(b.symbol, b.direction, round(b.confidence, 2)) for b in bias.biases],
    )
    return bias
```

**同时删除原有的第1205–1206行**（f-string格式的旧日志，避免重复打印）：
```python
    # 删除以下两行（原第1205–1206行）：
    logger.info(f"Layer 1 decision | market_state={bias.market_state} | "
                f"biases={[(b.symbol, b.direction, round(b.confidence, 2)) for b in bias.biases]}")
```
新的 `logger.info(...)` 已在上方插入替代它。

- [ ] **Step 5: 运行全套测试**

```bash
pytest tests/ -v --tb=short 2>&1 | tail -20
```
预期：全部通过

- [ ] **Step 6: 提交**

```bash
git add services/ai_decision/app.py tests/test_ai_decision_layer1.py
git commit -m "feat(stability): add Layer1 55min debounce to prevent cascading high-frequency decisions"
```

---

## 最终验证

- [ ] **运行完整测试套件**

```bash
pytest tests/ -v 2>&1 | tail -15
```
预期：全部通过，数量 ≥ 138 + 新增约10个测试

- [ ] **确认V9参数已生效**

```bash
python3 -c "
from services.ai_decision.config_loader import load_config
p = load_config()
checks = [
    ('MAX_GROSS', p['MAX_GROSS'], 0.30),
    ('CAP_ALT', p['CAP_ALT'], 0.08),
    ('AI_TURNOVER_CAP', p['AI_TURNOVER_CAP'], 0.03),
    ('POSITION_PROFIT_TARGET_BPS', p['POSITION_PROFIT_TARGET_BPS'], 25.0),
    ('COOLDOWN_MINUTES', p['COOLDOWN_MINUTES'], 20),
    ('DAILY_DRAWDOWN_HALT_USD', p['DAILY_DRAWDOWN_HALT_USD'], 2.0),
]
for name, actual, expected in checks:
    status = 'OK' if actual == expected else f'FAIL (got {actual}, want {expected})'
    print(f'  {name}: {actual} [{status}]')
"
```
预期：所有参数显示 `OK`

- [ ] **提交最终文件（如有遗漏）**

```bash
git status
git add config/trading_params.json services/ai_decision/config_loader.py \
    services/ai_decision/app.py tests/test_config_loader.py \
    tests/test_ai_decision_profit_target.py \
    tests/test_ai_decision_layer1.py tests/test_ai_decision_layer2.py \
    tests/test_ai_decision_capital.py tests/test_ai_decision_utils.py
git commit -m "chore: V9 improvements complete — JSON config + V9 parameters + logic fixes"
```
