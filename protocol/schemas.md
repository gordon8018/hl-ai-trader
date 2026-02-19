
# 数据模型（Schema）规范

所有模型建议用 Pydantic 定义，并保持 JSON 可序列化。

## 0. Envelope（env）

每条消息必须包含 `env`：

- event_id: UUID
- ts: UTC ISO8601（秒精度）
- source: 服务名
- cycle_id: 分钟周期 ID（如 20260218T1600Z）
- schema_version: 字符串（固定 "1.0"）
- retry_count: 重试次数（整数，默认 0）

消息结构：

```json
{
  "env": { ... },
  "data": { ... },
  "event": "optional_event_name"
}
```

## 1. FeatureSnapshot1m（md.features.1m）

字段：
- asof_minute: string（分钟对齐 UTC）
- universe: [string]
- mid_px: {symbol: number}
- ret_1m/ret_5m/ret_1h: {symbol: number}
- vol_1h: {symbol: number}
- spread_bps/book_imbalance/liq_intensity/liquidity_score: {symbol: number}（可选/增强）

## 2. StateSnapshot（state.snapshot）

字段：
- equity_usd: number
- cash_usd: number
- positions: {symbol: Position}
- open_orders: [OpenOrder]
- health: object

Position：
- symbol: string
- qty: number
- entry_px: number
- mark_px: number
- unreal_pnl: number
- side: LONG/SHORT/FLAT

OpenOrder：
- client_order_id: string
- exchange_order_id: string|null
- symbol: string
- side: BUY/SELL
- px: number
- qty: number
- status: string

## 3. TargetPortfolio（alpha.target）

字段：
- asof_minute: string
- universe: [string]
- targets: [{symbol, weight}]
- cash_weight: number
- confidence: number (0..1)
- rationale: string (<=800 chars)
- model: {name, version}
- constraints_hint: object（可选）

TargetWeight：
- symbol: enum(universe)
- weight: number（权益比例；可允许负数做空，MVP 可先只非负）

## 4. ApprovedTargetPortfolio（risk.approved）

字段：
- asof_minute: string
- mode: NORMAL/REDUCE_ONLY/HALT
- approved_targets: [TargetWeight]
- rejections: [Rejection]
- risk_summary: object

Rejection：
- symbol: string or "*"
- reason: string
- original_weight: number
- approved_weight: number

## 5. ExecutionPlan（exec.plan）

字段：
- cycle_id: string
- plan_type: TWAP_REBALANCE
- slices: [SliceOrder]
- limits: {max_orders_per_min, max_cancels_per_min}
- idempotency_key: string

SliceOrder：
- symbol: string
- side: BUY/SELL
- qty: number
- order_type: LIMIT/IOC/ALO
- px_guard_bps: int
- timeout_s: int
- slice_idx: int

## 6. OrderIntent（exec.orders）

字段：
- intent_id: UUID
- action: PLACE/CANCEL/REPLACE
- symbol, side, qty, order_type
- limit_px: number|null
- tif: string|null
- client_order_id: string
- slice_ref: string|null
- reason: string

## 7. ExecutionReport（exec.reports）

字段：
- client_order_id: string
- exchange_order_id: string|null
- symbol: string
- status: ACK/PARTIAL/FILLED/CANCELED/REJECTED
- filled_qty: number
- avg_px: number
- fee: number
- latency_ms: int
- raw: object|null（截断）
