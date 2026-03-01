# Hyperliquid 分钟级/组合级 AI 自动交易系统实现文档（Redis Streams + Python）
## 1. 目标与范围
### 1.1 交易目标

- 交易标的（Perps）：BTC、ETH、SOL、ADA、DOGE

- 交易周期：1 分钟（可配置）

- 策略形态：组合级/分钟级（目标权重/目标仓位），非 tick/HFT

- 上线方式：主网小资金灰度，支持一键熔断、可重启恢复、全链路可审计

### 1.2 核心原则

1. AI 不直接下单：AI 输出 TargetPortfolio（目标权重/仓位）；执行层负责将目标转换为订单，并确保风控与可靠性。

2. 状态以交易所为准：本地状态是镜像；断线/重启后必须通过 info 对账校准（时间范围接口分页最多 500 需处理）。

3. 风险先于收益：风控网关对 AI 输出做硬约束裁剪与熔断（NORMAL/REDUCE_ONLY/HALT）。

## 2. Hyperliquid 接入要点（必须遵守）
### 2.1 WebSocket

- 主网 WS：wss://api.hyperliquid.xyz/ws，测试网：wss://api.hyperliquid-testnet.xyz/ws。

- 订阅消息格式：{"method":"subscribe","subscription":{...}}；部分数据会带 isSnapshot: true 作为快照标签。

### 2.2 HTTP /exchange 与 /info

- 下单：POST https://api.hyperliquid.xyz/exchange（limit 的 TIF：GTC/IOC/ALO）。

- 查询：POST https://api.hyperliquid.xyz/info（用于 allMids / clearinghouseState / openOrders / orderStatus 等）。

### 2.3 速率限制（必须做全局限速）

- REST 聚合权重：1200/min（按 IP）。

- exchange 权重：1 + floor(batch_length/40)；部分 info（如 allMids、clearinghouseState、orderStatus、l2Book）权重较高。

- 实现要求：执行层必须有令牌桶限速，并尽量 batch 下单以降低权重占用。

## 3. 总体架构（Python 微服务 + Redis Streams）
### 3.1 服务拆分（6 个进程）

1. market_data：获取行情并生成 1m 特征快照 FeatureSnapshot1m。

2. portfolio_state：账户状态镜像与对账；维护 StateSnapshot（权威镜像）。

3. ai_decision：AI 决策输出 TargetPortfolio（目标权重/仓位）。

4. risk_engine：风控裁剪输出 ApprovedTargetPortfolio。

5. execution：将目标变成订单（TWAP 分片执行），负责幂等、限速、跟踪与撤单等。

6. web_ui/monitor：可视化与告警（非 MVP 必需，但建议保留接口）。

### 3.2 数据流（每分钟闭环）

market_data → ai_decision → risk_engine → execution → portfolio_state（对账/订单状态跟踪）
同时全链路写 audit.logs（用于复盘与回测 replay）。

## 4. Redis Streams 事件总线设计（核心）
### 4.1 Streams 列表（固定命名）

- md.features.1m：每分钟特征快照（触发决策）
- state.snapshot：账户状态镜像快照
- state.events：订单状态更新、对账差异、异常等增量事件
- alpha.target：AI 输出（目标组合）
- risk.approved：风控输出（裁剪后目标）
- exec.plan：执行计划（TWAP 分片）
- exec.orders：订单意图（PLACE/CANCEL/REPLACE）
- exec.reports：执行回报（ACK/PARTIAL/FILLED/CANCELED/REJECTED）
- ctl.commands：控制命令（HALT/RESUME/REDUCE_ONLY/参数更新）
- audit.logs：审计日志（输入/输出/裁剪/执行摘要/错误）

### 4.2 消费组（Consumer Groups）

- 每个 stream 使用 XREADGROUP + XACK，服务崩溃时通过 PEL（pending entries list）可恢复处理。

- 建议组名：
    - ai_grp 读 md.features.1m
    - risk_grp 读 alpha.target
    - exec_grp 读 risk.approved
    - state_reports_grp 读 exec.reports

### 4.3 DLQ（Dead Letter Queue）

- 统一重试字段：env.retry_count

- 超过 MAX_RETRIES 后写：

    - dlq.alpha.target

    - dlq.risk.approved

    - dlq.exec.orders

- DLQ 写入时必须同时写 audit.logs 触发告警。

## 5. 统一消息 Envelope 规范（所有事件必须有）

字段：

- event_id：UUID
- ts：UTC ISO 时间
- source：服务名
- cycle_id：分钟周期 ID（如 20260218T1600Z）
- schema_version：字符串（固定 "1.0"）
- retry_count：重试次数

现要求：所有 stream 消息 payload 统一为 { "env": {...}, "data": {...} }（或 event 类消息允许加 event 字段），并将 data 以 JSON 存入 Redis Streams 单字段（例如 p）避免 field 数量限制。

## 6. 数据模型（Pydantic / JSON Schema）
### 6.1 FeatureSnapshot1m（md.features.1m）

- asof_minute（分钟对齐）
- universe（5 币）
- mid_px：{symbol: mid}
- ret_1m/ret_5m/ret_1h：{symbol: r}
- vol_1h：{symbol: vol}
- 可选：spread_bps、book_imbalance、liq_intensity、liquidity_score

MVP 可用 info: allMids 计算 mid/return/vol；增强版用 WS l2Book 计算 spread/imbalance。

### 6.2 StateSnapshot（state.snapshot）

- equity_usd、cash_usd
- positions：{symbol: {qty, entry_px, mark_px, unreal_pnl, side}}
- open_orders：[{client_order_id, exchange_order_id, symbol, side, px, qty, status}]
- health：{last_reconcile_ts, reconcile_ok, ...}

对账来源：
- info: clearinghouseState、openOrders、allMids、必要时 userFills 翻页。

### 6.3 TargetPortfolio（alpha.target）

- targets：[{symbol, weight}]（weight 为权益比例；可允许负数代表做空，但 MVP 可先限制非负）
- cash_weight
- confidence：0~1
- rationale：短解释（<=800 字）
- model：{name, version}
- constraints_hint：{max_gross, max_net, turnover_cap}

### 6.4 ApprovedTargetPortfolio（risk.approved）

- mode：NORMAL | REDUCE_ONLY | HALT
- approved_targets
- rejections：[{symbol, reason, original_weight, approved_weight}]
- risk_summary：{gross, net, turnover, flags[]}

### 6.5 ExecutionPlan（exec.plan）

- cycle_id
- plan_type：TWAP_REBALANCE
- slices：[{symbol, side, qty, order_type, px_guard_bps, timeout_s, slice_idx}]
- limits：{max_orders_per_min, max_cancels_per_min}
- idempotency_key

### 6.6 OrderIntent（exec.orders）

- action：PLACE/CANCEL/REPLACE
- client_order_id（幂等键：cycle_id:symbol:slice_idx:side）
- symbol、side、qty、order_type、limit_px、tif
- slice_ref、reason

### 6.7 ExecutionReport（exec.reports）

- client_order_id、exchange_order_id
- status：ACK/PARTIAL/FILLED/CANCELED/REJECTED
- filled_qty、avg_px、fee、latency_ms
- raw（截断后的交易所回包/错误）

## 7. AI 决策模块（核心功能需求）
### 7.1 设计目标

- 生成稳健、低换手、受约束的目标组合，而不是“情绪化高频改仓”。
- 必须输出可审计：输入快照摘要、模型版本、原始输出与最终输出差异。

### 7.2 实现形态：LLM + 结构化输出 + 稳定器

- LLM 输出严格 JSON：targets/cash_weight/confidence/rationale

- 输出必须经过：

    1. Pydantic 校验（解析失败直接降级 baseline）

    2. cap & scale（per-symbol cap、gross/net cap）

    3. 低置信度降杠杆（confidence < threshold → gross scale down）

    4. EWMA 平滑（减少分钟抖动）

    5. turnover cap（相对当前组合，若换手过大则缩放 delta）

### 7.3 失败降级

LLM 请求失败 / JSON 解析失败 / 输出越界：使用 baseline（动量/波动缩放）生成目标。

所有失败必须写入 audit.logs（字段：used=baseline_fallback、error）。

### 7.4 建议（可选增强）

- “二阶段 AI”更稳：LLM 先做 regime/risk multiplier，权重由可解释 optimizer 输出（更可控）。
- 提供 replay 工具：从历史 md.features.1m + state.snapshot 重放生成信号序列。

## 8. 风控模块（risk_engine）
### 8.1 默认阈值（主网小资金建议保守）

- max_gross = 0.40
- max_net = 0.20
- 单币 cap：BTC/ETH 0.15；SOL/ADA/DOGE 0.10
- turnover_cap_per_cycle = 0.10
- 回撤熔断（可选，从 equity 轨迹计算）：DD>=2% → REDUCE_ONLY；DD>=3% → HALT
- 异常保护：断线/对账失败/订单回报异常 → HALT

### 8.2 风控输出

- 必须输出 mode，并给出 rejections 与原因（便于审计）。

## 9. 执行模块（execution）
### 9.1 执行策略：Rebalance TWAP（默认）

- 将目标权重变化转为 qty = (Δw * equity) / mid
- Close-first：先减仓后加仓（先释放风险）
- 分片：默认 TWAP_SLICES=3，间隔 SLICE_INTERVAL_S=20
- 价格护栏：px_guard_bps（BUY: mid*(1+g), SELL: mid*(1-g)）
- 超时：SLICE_TIMEOUT_S=20，超时后 CANCEL；可选一次 REPLACE（更靠近 mid）

### 9.2 幂等（必须）

- client_order_id = cycle_id:symbol:slice_idx:side
- Redis 记录：dedup:{client_order_id} -> {status, exchange_order_id}
- 重试时若 dedup 状态已 >= ACK，不得重复下单（改为查询/等待对账）。

### 9.3 订单状态跟踪（必须）

- 通过 info: orderStatus 轮询跟踪 exchange oid 的终态（filled/canceled/rejected）。
- 同时由 portfolio_state 定期对账校准（最终以 clearinghouseState/openOrders 为准）。

### 9.4 限速（必须）

- Redis 全局令牌桶：
    - max_orders_per_min
    - max_cancels_per_min
- 限速触发时：跳过分片并写 audit.logs（rate_limited）。

## 10. 账户状态与对账（portfolio_state）
### 10.1 周期对账（权威）

- 每 10s（可配置）调用：
    - clearinghouseState（仓位/保证金/账户价值）
    - openOrders（挂单）
    - allMids（mark/mid 近似）
- 产出 state.snapshot 并维护 Redis key：latest.state.snapshot（TTL=60s）

### 10.2 订单状态事件

- 订阅 exec.reports 收集 exchange_order_id，写入 active.exchange.oids set
- 每 5s 轮询 orderStatus，将结果写入 state.events，并在检测到终态后从 set 移除。

### 10.3 分页处理

- info 中涉及时间范围的接口最多返回 500，需要用最后 timestamp 翻页。

## 11. market_data（行情与特征）
### 11.1 MVP（稳定优先）

- 每 2 秒调用 info: allMids 获取 mid，计算 1m/5m/1h 收益与 1h 实现波动率。
- 每分钟生成并写入 md.features.1m（作为整个系统“心跳触发器”）。

### 11.2 增强版（可选）

- WS 订阅 l2Book 以计算 spread/imbalance/liquidity_score（订阅协议见官方）。

## 12. 控制面与安全开关（ctl.commands）

- 必须支持写入 ctl.commands 的命令：
    - HALT：停止交易（不下新单）
    - REDUCE_ONLY：只允许减仓，不允许加仓
    - RESUME：恢复 NORMAL
- 可选：动态修改参数（max_gross、turnover、slices 等）
- 执行层必须在每个 cycle 开始读取最新控制状态（可用 Redis key 缓存最新命令）。

## 13. 审计与可观测性
### 13.1 audit.logs（强制）

- 记录至少这些事件：
    - md.features.1m：产出时间、缺失币种
    - alpha.target：used(llm/baseline)、confidence、gross、turnover、raw_llm（可截断）
    - risk.approved：裁剪原因、mode、gross/net/turnover
    - exec.plan：分片数量、总下单数
    - exec.reports：ACK/REJECT/CANCEL/FILL 汇总、错误原因
    - dlq：进入死信的原始 payload 与原因

### 13.2 Prometheus 指标（建议）

- 每服务：bus_messages_in_total、bus_messages_out_total、service_errors_total、service_latency_seconds
- 交易指标：orders/min、reject rate、cancel rate、fill latency（可后续扩展）

## 14. 配置参数（.env 必需项）

- Redis/Postgres：
    - REDIS_URL
    - POSTGRES_DSN（可选，MVP 可先不落库）
- Universe：
    - UNIVERSE=BTC,ETH,SOL,ADA,DOGE
    - CYCLE_SECONDS=60
- Risk：
    - MAX_GROSS, MAX_NET, CAP_BTC_ETH, CAP_ALT, TURNOVER_CAP
- Execution：
    - TWAP_SLICES, SLICE_INTERVAL_S, SLICE_TIMEOUT_S, PX_GUARD_BPS
    - MAX_ORDERS_PER_MIN, MAX_CANCELS_PER_MIN
- Hyperliquid：
    - HL_WS_URL=wss://api.hyperliquid.xyz/ws
    - HL_HTTP_URL=https://api.hyperliquid.xyz
    - HL_ACCOUNT_ADDRESS
    - HL_PRIVATE_KEY
- 安全：
    - DRY_RUN=true/false（上线前必须先 true 跑通）
- LLM：
    - AI_USE_LLM
    - AI_LLM_ENDPOINT（OpenAI-compatible）
    - AI_LLM_API_KEY
    - AI_LLM_MODEL
    - AI_SMOOTH_ALPHA, AI_MIN_CONFIDENCE, AI_LOW_CONF_SCALE

## 15. 实现里程碑（建议严格按顺序）

- M1：闭环骨架（不下单）
    - market_data 产出 md.features.1m
    - portfolio_state 产出 state.snapshot
    - ai_decision 输出 baseline alpha.target
    - risk_engine 输出 risk.approved
    - execution 生成 exec.plan 与 exec.orders，但 DRY_RUN=true（不触发真实交易）
- M2：主网小资金真实交易（低风险）
    - DRY_RUN=false，只交易 BTC/ETH，cap 很小（如 2~5%）
    - 验证订单 ACK、状态跟踪、对账一致性、限速不触发
    - 加入 ctl.commands 一键 HALT
- M3：AI 上线（LLM）
    - ai_decision 改为 LLM+校验+平滑+换手约束，失败自动降级
    - 增加审计：raw_llm、used、turnover、gross
- M4：增强（可选）
    - WS l2Book 特征
    - 二阶段 AI（regime → optimizer）
    - 历史 replay 回测工具链

## 16. Codex 实现注意事项（强制要求）

- 绝不在执行层信任 AI 输出：执行前必须经过 risk_engine。
- 所有网络调用必须有超时与重试上限；失败写 audit.logs。
- 所有 stream 消息必须带 env，且数据必须可被 Pydantic 验证。
- 幂等与限速是能否实盘跑的关键，不得省略。
- 对账永远以交易所为准，本地状态只是镜像。
