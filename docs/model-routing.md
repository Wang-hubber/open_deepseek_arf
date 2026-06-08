# Model Routing — Ordered Fallback Chain & KV Cache

ARF 将 OS 的多级缓存层次和异构调度策略适配到多模型场景：廉价模型处理简单任务，强大模型处理复杂任务，框架后台任务走专用 system model 通道。模型选择通过有序降级链实现——主模型失败时自动切换到备用模型。

---

## 1. OS 方案演进

> 本章描述 OS 如何处理缓存层次与异构调度，作为 ARF 设计思路的参考。非严格技术对标。

### 1.1 CPU 缓存层次

**为什么需要缓存** — CPU 速度远快于内存（~100ns vs ~1ns per cycle）。如果每次访问都穿透到内存，CPU 大部分时间在等待。缓存利用程序的**时间局部性**（刚访问的数据很可能再访问）和**空间局部性**（临近数据很可能被访问）。

**三级缓存演进**：

- **L1 Cache**（Pentium, 1993）：指令/数据分离，~32KB，1-2 周期延迟。集成在 CPU 核心内
- **L2 Cache**（Pentium Pro, 1995）：统一缓存，~256KB-1MB，~10 周期。从主板移入芯片封装
- **L3 Cache**（Core i7, 2008）：所有核心共享，~8-32MB，~40 周期

层次化的本质是**容量-延迟权衡**：越靠近核心越快但越小。Cache Line 以 64 字节行为单位——即使程序只读 1 字节，缓存也加载周围 64 字节（空间局部性的硬件实现）。

### 1.2 big.LITTLE 异构调度

ARM 2011 年引入 big.LITTLE：高性能大核与高能效小核组合在同一 SoC。调度演进：

- **Cluster Migration**：整 cluster 切换，粒度最粗
- **In-Kernel Switcher (IKS)**：每对 big-LITTLE 虚拟为一个核心，利用率低，已淘汰
- **Heterogeneous Multi-Processing (HMP, 2013+)**：全局调度器同时看到所有核心。根据任务负载历史分配——轻任务到小核，重任务到大核。当前 Linux CFS 标准模式
- **Energy Aware Scheduling (EAS, 2015+)**：加入能耗模型，优先填满小核，大核仅在需要时唤醒

### 1.3 分支预测

CPU 遇到分支指令时，**分支预测器**根据历史模式猜测方向，提前取指，避免流水线停顿。动态预测的准确率 > 95%。预测失败的代价是刷新流水线（15-20 周期），但预测正确的收益远大于偶尔错误。

### 1.4 对 ARF 的启发

缓存层次启示了任务分级：不是所有请求都需要最强模型。big.LITTLE 启示了异构降级：主模型不可用时切换到备用模型，而不是直接报错。分支预测启示了退化成本可控：降级到更强模型的成本低于降级到更弱模型的重试成本。

**OS → ARF 概念映射**：

| OS 概念 | ARF 映射 | 说明 |
|---------|----------|------|
| L1/L2/L3 Cache | KV Cache | OS 权衡时间成本（~1ns vs ~100ns），Agent 权衡推理成本 |
| big.LITTLE (HMP) | 有序降级链 `ModelDegrader` | 按配置顺序尝试模型，失败时自动降级 |

---

## 2. ARF 当前实现

模型调度分为两条路径：**用户任务**（通过有序降级链调用）和**框架任务**（固定使用 system model）。

### 2.1 架构总览

```
用户消息进入
    │
    ▼
ControlPlane 调用 _call_model / _stream_model
    │
    ▼
ModelDegrader.chat_complete(messages, tools)
    │
    ├─ 适配器 0 (deepseek-v4-pro)     ← 首选
    │   ├─ 成功 → 返回结果
    │   └─ 失败 (5xx/429/网络错误) → 降级到下一个
    │
    ├─ 适配器 1 (deepseek-v4-flash)   ← 备用
    │   ├─ 成功 → 返回结果
    │   └─ 失败 → 持续降级
    │
    └─ ... 全部失败 → 抛出异常，ErrorPolicy 处理

框架后台任务（上下文压缩、任务分类、Agent 间调度）
    │  不走 ModelDegrader
    │  直接使用 system model（配置为 quick）
    │  thinking_enabled: false, temperature: 0.3
    ▼
专用适配器（_system_model_call）
```

### 2.2 两层调度

| 调度层 | 负责内容 | 模型选择方式 | 配置位置 |
|--------|----------|-------------|----------|
| **用户任务调用** | 每 turn 根据有序降级链调用模型 | `ModelDegrader` — 按 `agent_models` 顺序尝试 | `agent_models` / `model_defs` |
| **框架任务分配** | 压缩、分类、handoff 等后台操作 | 固定 system model | `advanced.system_model` |

### 2.3 ModelAdapter

`arf/core/model_adapter.py`。统一的 OpenAI 兼容端点封装，单个适配器实例对应一个模型端点：

```python
class ModelAdapter:
    def __init__(self, config: dict, context_window: int = 1048576):
        self.client = AsyncOpenAI(
            base_url=config.get("base_url"),
            api_key=config.get("api_key", "") or "sk-placeholder",
        )
        self.model_name = config.get("model_name", "")
        self.context_window = ...
```

**功能**：

- **指数退避重试**：429/5xx 等瞬时错误自动重试（默认 3 次，退避基数 1.5x）
- **DeepSeek thinking 翻译**：`thinking_enabled` → `extra_body["thinking"]` 的 enabled/disabled 格式
- **流式支持**：`chat_stream_full()` 产出以下四种事件：

| 事件类型 | 字段 | 说明 |
|----------|------|------|
| `chunk` | `type`, `content`, `reasoning?` | 文本增量（DeepSeek deep-thinking 时附带 reasoning 字段） |
| `tool_call` | `type`, `name`, `arguments`, `id` | 累积完成的工具调用（finish_reason="tool_calls" 时产出） |
| `usage` | `type`, `prompt_tokens`, `completion_tokens`, `total_tokens` | 流结束时统计用量（出现在 usage chunk 上） |
| `error` | `type`, `code`, `detail` | API 调用失败时产出（status_code + message） |

- **空 key 保护**：`api_key` 为空时使用 `"sk-placeholder"` 防止 OpenAI SDK 拒绝 falsy 值
- **参数翻译**：config 中的 `thinking_enabled`、`reasoning_effort` 等 provider 特有参数自动映射到 `extra_body`

### 2.4 ModelDegrader — 有序降级链

`arf/core/model_degrader.py`。将多个 `ModelAdapter` 包装为有序降级链：

```python
class ModelDegrader:
    def __init__(self, adapters: list) -> None:
        if not adapters:
            raise ValueError("At least one model adapter required")
        self._adapters = adapters
```

**降级逻辑**：

```
ModelDegrader.chat_complete()
    │
    ├─ adapter[0] → 成功？ → 返回
    │     └─ 失败 (5xx/429/网络错误) → degrade
    │
    ├─ adapter[1] → 成功？ → 返回
    │     └─ 失败 → degrade
    │
    └─ ... 全部失败 → 抛出最后一次异常
```

**降级触发条件**：
- HTTP 5xx（服务端错误）
- HTTP 429（速率限制）
- 无 HTTP 状态码的错误（网络超时、连接失败）
- 不降级：4xx 客户端错误（参数错误、认证失败等）

```python
def _should_degrade(self, error: Exception) -> bool:
    status = (
        getattr(error, 'status_code', None)
        or getattr(error, 'status', None)
        or getattr(error, 'http_status', None)
    )
    if status is not None:
        return status >= 500 or status == 429
    # No HTTP status → assume transient (network/timeout/etc), degrade
    return True
```

**流式降级**：`chat_stream_full()` 也支持降级，但仅在流产生任何内容之前降级。一旦第一个 chunk 发出，就锁定该适配器，中游失败不恢复。

**退化行为**：当所有适配器全部失败时，异常向上抛出到引擎层，由 `ErrorPolicy`（`DefaultErrorPolicy`）决定处理策略。

### 2.5 引擎集成

在 `BaseAgent.init()` 中构建 `ModelDegrader`（`base.py`）：

```python
# Build ModelDegrader from agent_models config
agent_models = config.get_agent_model_configs()
if agent_models:
    for mcfg in agent_models:
        _deg_adapters.append(ModelAdapter({
            "model_name": mcfg.model,
            "api_key": ...,
            "base_url": ...,
            **mcfg.kwargs,
        }))
    _model_degrader = ModelDegrader(_deg_adapters)
```

组装后，`_call_model` 和 `_stream_model` 闭包包装 `_model_degrader` 的调用，注入到 `ControlPlane`：

```python
async def _call_model(messages: list[dict], model_name: str = "", tools=None) -> dict:
    msg = await _model_degrader.chat_complete(messages, tools=tools)
    ...
```

`ControlPlane` 通过 `set_call_model` / `set_stream_model` 接收这些闭包，在 `_dispatch` 阶段调用。

### 2.6 System Model — 框架后台任务的专用模型

框架运行时会触发一系列后台操作——上下文压缩、路由分类、Agent 间调度（handoff）。这些操作对质量要求不高（分类错了只是多用一次 deep，摘要差点也能继续对话），但对延迟和成本敏感（每次 turn 都要跑）。如果走用户模型通道，这些隐形消耗会大幅推高 token 用量。

> **注意**：记忆抽取/检索原为 system model 消费者，现已迁移到 `arf/plugins/memory/` 插件架构。插件以 subprocess 方式运行，内部自行构造 ModelAdapter（不共享 Agent 的 `_system_model_call`）。详见 `arf/plugins/memory/tools/memory_extract/extractor.py`。

ARF 的方案是：**框架后台任务统一由一个廉价模型实例执行**，称为 system model。它与用户任务模型共享同一个 API key（`api_key_env`），不单独认证。

**定义流程**（`base.py`）：

```
agent.yaml                    config.models              ModelAdapter
advanced.system_model: quick → lookup by name → quick → _system_model_call
                                     ↓ 找不到
                              config.models[0]（回退）
                                     ↓ 为空
                              None → _system_model_call = None
```

- 从 `config.models` 中查找 `advanced.system_model` 指定的模型（如 `quick`）
- 找不到则回退到 `config.models[0]`
- 用低温度（0.3）、关闭 thinking、`max_tokens=1024` 创建适配器
- 最终产物是一个 `_system_model_call(prompt: str) -> str` 闭包——输入简短 prompt，返回纯文本

**三个活跃消费者及其退化行为**：

| 功能 | 有 system_model | 无 system_model（退化） |
|------|----------------|----------------------|
| **压缩摘要** | `_summarize` — LLM 将旧轮次压缩为结构化摘要（7 个维度），追加到 `context_summary` | `_summarize = None` — 旧消息直接丢弃，不生成摘要 |
| **路由分类** | `_classify` — LLM 判断 query 复杂度（medium / complex） | `classify()` 始终返回 `"medium"` — 全部走 quick 模型 |
| **Handoff 分类** | `HandoffManager` — LLM 判断 handoff 目标 Agent | handoff 规则仍基于 `trigger` 字段匹配生效，但无 LLM 辅助判断 |

所有退化都是静默的——不报错、不阻塞主流程。框架通过 `if _system_model_call:` 模式检查是否存在，不存在时回退到确定性规则方案。

**配置**：

```yaml
advanced:
  system_model: quick      # 系统后台模型，须与 models/ 中某个模型同名
```

**设计约束**：
- system model 与用户模型共享同一个 API key（`api_key_env`），不单独认证
- system model 在 BaseAgent 初始化时创建一次，生命周期与 Agent 相同
- 不存在时不报错——框架静默退化到规则方案，面向无 LLM 环境

### 2.7 降级链

降级链跨 ModelDegrader 和 ErrorPolicy 两层协作完成：

| 层级 | 触发条件 | 行为 | 负责层 |
|------|----------|------|--------|
| ModelAdapter 重试 | 429/5xx/网络错误 | 指数退避重试（默认 3 次） | **ModelAdapter** — `_call_with_retry()` |
| ModelDegrader 降级 | 重试耗尽后仍失败 | 切换到下一个适配器 | **ModelDegrader** — `chat_complete()` 循环 |
| 全部适配器失败 | 所有适配器均不可用 | 向上抛出异常 | **ModelDegrader** — 抛出 `last_error` |
| ErrorPolicy 处理 | ModelDegrader 抛出异常 | 根据 `model_5xx_action` 决定 abort/retry/fallback | **ErrorPolicy** — `on_model_error()` |

**退化链示例**：

```
deepseek-v4-pro (首选) → 503 Service Unavailable
    → ModelAdapter 重试 3 次 → 仍然 503
    → ModelDegrader 降级到下一个适配器
    → deepseek-v4-flash (备用) → 成功
    → 对话继续，用户无感知
```

如果所有模型都失败，错误上升到 `ControlPlane` 层，由 `_handle_error` 和 `ErrorPolicy` 共同决定是否终止会话。

### 2.8 KV Cache — 框架有意不介入

KV cache 由推理侧（DeepSeek API）在服务端管理，框架不操作缓存生命周期。理由：
- 命中缓存率很高，算力成本支出已经不需要严格的管控
- 框架侧介入需感知推理引擎内部状态，增加耦合
- Token 感知的压缩策略已在前端减少了发送到 API 的上下文量

如果未来需要框架侧 KV cache 管理，可能的介入点：跨 turn 复用 system prompt 部分的 KV cache（prompt 不变时不重复编码），或多 session 间共享缓存前缀。

### 2.9 配置

```yaml
model_defs:                                     # 模型定义（新格式）
  - model: deepseek-v4-pro
    api_base: https://api.deepseek.com/v1
    api_key_env: DEEPSEEK_API_KEY
    kwargs:
      temperature: 0.0
      max_tokens: 8192

  - model: deepseek-v4-flash
    api_base: https://api.deepseek.com/v1
    api_key_env: DEEPSEEK_API_KEY
    kwargs:
      temperature: 0.0
      max_tokens: 4096

agent_models:                                   # 降级链顺序（按配置先后）
  - model: deepseek-v4-pro                      # 首选
  - model: deepseek-v4-flash                    # 备用

advanced:
  system_model: deepseek-v4-flash               # 系统后台模型，框架任务共用
```

`agent_models` 数组的顺序决定了降级链的优先级。列表中的第一个模型是首选，后续模型是降级备用。

### 2.10 引擎集成

在 `ControlPlane` 的每次 turn 中，`_dispatch` 阶段调用 `self._call_model` / `self._stream_model` 闭包。这些闭包由 `BaseAgent.init()` 创建，内部使用 `ModelDegrader` 实现有序降级。

每次模型调用后，引擎将 `usage.total_tokens` 存储到 state 中，供 `SlidingWindowCompactor` 在下一轮判断压缩时机。

---

## 3. 演进方向

### 3.1 对标 OS 最佳实践：三级分类器

当前只有两级（medium/complex）。Linux CFS 在 HMP 模式下对所有任务做连续的负载跟踪（PELT），而非二分。

演进方向：增加 `light` 级别，形成三级分类：

- **light**：纯闲聊、确认 → 最小模型（延迟最优）
- **medium**：单工具调用、文件操作 → quick
- **complex**：多步推理、代码生成 → deep

分类器从二分类改为三分类，prompt 调整即可。

### 3.2 连续负载跟踪

当前分类是每 turn 独立判断，不参考历史。可以维护任务复杂度历史：如果前 N 轮连续被分类为 complex，后续优先用 deep（任务可能持续复杂）；用户切换话题时重置历史。类似分支预测器的自适应机制。

### 3.3 探索性方向

**模型硬件化（LLM as Hardware）**：随着专用推理芯片（Groq、Cerebras、LPU）成熟，特定任务（记忆抽取、分类、embedding）的小模型可固化为"硬件指令"——框架向专用芯片发起固定格式的推理请求，延迟从秒级降至毫秒级。类似 OS 将浮点运算卸载到 FPU。

**基于负载的自动路由**：当前只看 query 文本，不看系统负载。如果 deep 模型的 API 延迟突增，可自动将 medium 级别的请求路由到 quick。类似 OS 的 load balancing——CPU 队列过长时迁移任务。

**session 间 prompt 缓存共享**：多个 session 的 system prompt 高度重叠，可在框架侧缓存 system prompt 的 embedding 前缀。类似 OS 共享库（.so）在进程间共享只读页面——同一份物理内存映射到多个虚拟地址空间。
