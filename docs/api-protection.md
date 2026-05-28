# API Protection — Rate Limiting & Circuit Breaking

## 1. OS 方案演进

### 1.1 I/O 调度与限流

OS kernel 不能让一个失控进程无限往磁盘发 I/O——用 **cgroup blkio** 限制 IOPS 和吞吐量。每个 cgroup 分配独立的 I/O 预算，超额请求被 throttle，防止单个进程耗尽磁盘带宽。

ARF 面对同样的问题：LLM API 有 rate limit（如 DeepSeek API 每分钟 60 次），当 Agent 快速连续调用模型（多轮对话 + 工具调用 + 系统后台任务），很容易触发 API 限流。传统方案是在调用层硬编码 `time.sleep()`，但不可配置、不可观测。

**Token bucket** 算法是 OS I/O throttling 的标准实现：以固定速率补充令牌，请求消费令牌，桶空时拒绝（不等待）。这恰好映射到 API rate limiting——允许短时突发（burst = bucket capacity），平滑长期速率（rate = tokens/sec）。

### 1.2 设备故障隔离

磁盘反复报错时，kernel 把它 **fence 掉**——停止一切 I/O 请求，定期 probe 看恢复了没有。这防止故障设备拖垮整个存储栈。

ARF 面对同样的问题：当一个模型 endpoint 持续返回 500/503，没有理由继续往它发请求——每次调用都在浪费 token 和延迟。**Circuit breaker** (熔断器) 是设备 fencing 的精确类比：

| OS | ARF |
|----|-----|
| 检测连续 I/O error | 检测连续 API 5xx |
| fence 设备 → 停止 I/O | OPEN → 拒绝请求 |
| 定期 probe | HALF_OPEN → 发送探测请求 |
| probe 成功 → 恢复 | CLOSED → 恢复正常调用 |
| 每次 fence 后 probe 间隔翻倍 | exponential cooldown |

---

## 2. 当前实现

### 2.1 架构

```
BaseAgent._inject_model_calls()
  └─ ModelCallProtector
       ├─ TokenBucket (per api_base)  → rate limiting
       ├─ CircuitBreaker (per model)  → fault isolation
       └─ EventBus                   → observability
            ↓
       wrapped _call_model / _stream_model
```

保护层以 decorator 模式注入 `_call_model` 和 `_stream_model` closures，GraphEngine 和 ModelAdapter 零侵入。

### 2.2 TokenBucket — `arf/protection/rate_limiter.py`

```python
class TokenBucket:
    capacity: float          # max burst
    rate: float              # tokens/sec refill
    tokens: float            # current count
    async def acquire() -> bool  # try-consume, never blocks
```

- **per api_base** — 同一 API 端点的不同模型共享限流
- **非阻塞** — `acquire()` 返回 `False` 时调用方立即获知 (raise `RateLimitError`)
- **线程安全** — `asyncio.Lock` 保护

### 2.3 CircuitBreaker — `arf/protection/circuit_breaker.py`

三态状态机：

```
CLOSED ──(failure_threshold 次连续失败)──▶ OPEN
OPEN   ──(open_duration 到期)───────────▶ HALF_OPEN
HALF_OPEN ──(成功)──────────────────────▶ CLOSED
HALF_OPEN ──(失败)──────────────────────▶ OPEN (cooldown *= multiplier)
```

关键参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `failure_threshold` | 3 | 连续失败 N 次 → OPEN |
| `base_cooldown` | 10s | 首次熔断冷却时间 |
| `cooldown_multiplier` | 2 | 每次重开后冷却翻倍 |
| `max_cooldown` | 300s | 冷却时间上限 |
| `half_open_max_requests` | 1 | HALF_OPEN 探测请求数 |

### 2.4 ModelCallProtector — `arf/protection/protector.py`

组合 TokenBucket + CircuitBreaker，提供两个 async 方法：

- `call_with_protection(raw_call, messages, model_name, tools)` → 用于 `_call_model`
- `stream_with_protection(raw_stream, messages, model_name, tools)` → 用于 `_stream_model`

通过 `model_map` 将引擎的 model_type (`"deep"`/`"quick"`) 映射到 api_base + model_name。

### 2.5 可观测性

5 种事件类型通过 EventBus → FileTraceStore 发射：

| 事件 | 触发条件 |
|------|---------|
| `rate_limited` | Token bucket 拒绝请求 |
| `circuit_opened` | 断路器熔断 (CLOSED→OPEN) |
| `circuit_half_open` | 断路器进入探测 (OPEN→HALF_OPEN) |
| `circuit_closed` | 断路器恢复 (HALF_OPEN→CLOSED) |
| `breaker_blocked` | OPEN 状态拒绝请求 |

### 2.6 Retry 简化

| 层 | 之前 | 之后 |
|----|------|------|
| ModelAdapter `_call_with_retry` | 3 次指数退避 | **保持** (瞬时错误: 429, network) |
| `DefaultErrorPolicy.on_model_error` | 最多 3 次 engine 级重试 | **移除 retry** — 保护层处理 |
| `_resolve_fallback` | 5xx → fallback 模型 | **保持** (breaker OPEN → fallback) |

### 2.7 配置

```yaml
advanced:
  protection:
    enabled: true
    rate_limit:
      requests_per_second: 5
      max_burst: 10
    circuit_breaker:
      failure_threshold: 3
      base_cooldown: 10s
      cooldown_multiplier: 2
      max_cooldown: 300s
      half_open_max_requests: 1
```

---

## 3. 演进方向

- **分布式限流**：多 Agent 实例共享 quota（Redis-backed token bucket），避免单机限流不足以保护 API endpoint
- **自适应阈值**：基于历史错误率动态调整 `failure_threshold`，而不是固定 3 次——高错误率时更快熔断，低错误率时更宽容
- **优先级队列**：系统后台任务（memory/routing）与用户请求使用不同优先级，系统任务被限流时不阻塞用户对话
- **Metrics 导出**：将 rate limit 次数、breaker 状态切换次数导出为 Prometheus metrics
