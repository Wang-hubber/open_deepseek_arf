# Model Routing — Multi-Model Scheduling & KV Cache

ARF 将 OS 的多级缓存层次和异构调度策略适配到多模型场景：廉价模型处理简单任务，强大模型处理复杂任务，框架后台任务走专用 system model 通道。

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

缓存层次启示了任务分级：不是所有请求都需要最强模型。big.LITTLE 启示了异构调度：后台任务跑专用廉价模型，用户任务按复杂度路由。分支预测启示了分类器：快速猜测任务复杂度并路由，猜错的代价（降级到廉价模型）远小于每次都上最强模型的浪费。

---

## 2. ARF 当前实现

模型调度分为两条路径：**用户任务路由**（按复杂度选择模型）和**框架任务分配**（固定使用 system model）。两者共享同一个廉价模型实例，不额外增加 API 连接。

### 2.1 架构总览

```
用户消息进入
    │
    ▼
TwoTierRouter.classify(query)
    │  LLM 分类器（复用 system model, deepseek-v4-flash, thinking disabled）
    │  返回 "medium" 或 "complex"
    │
    ├─ medium  → quick  (deepseek-v4-flash)
    │            简单聊天、单工具调用、文件 I/O
    │
    └─ complex → deep   (deepseek-v4-pro)
                   多步推理、代码生成、规划任务

框架后台任务（记忆抽取/检索、上下文压缩、任务分类）
    │  不走 TwoTierRouter
    │  直接使用 system model（配置为 quick）
    │  thinking_enabled: false, temperature: 0.3
    ▼
专用适配器（_system_model_call）
```

### 2.2 两层调度

| 调度层 | 负责内容 | 模型选择方式 | 配置位置 |
|--------|----------|-------------|----------|
| **用户任务路由** | 每 turn 根据 query 复杂度选模型 | `TwoTierRouter` + LLM 分类器 | `advanced.routing` |
| **框架任务分配** | 记忆、压缩、分类等后台操作 | 固定 system model | `advanced.memory.model` |

### 2.3 协议

`ModelRouter` 协议（`arf/core/protocols/routing.py`）：

```python
class ModelRouter(Protocol):
    async def route(self, query: str, history: list[dict]) -> str: ...
    async def classify(self, query: str) -> str: ...
    def fallback_from(self, model_name: str) -> str | None: ...
```

### 2.4 TwoTierRouter 实现

`arf/routing/two_tier.py`（22 行）。核心逻辑是将 LLM 分类结果映射到模型名：

```python
class TwoTierRouter:
    async def route(self, query: str, history: list[dict]) -> str:
        level = await self.classify(query)          # "medium" | "complex"
        return self._cfg.classify.get(level, self._cfg.default)

    async def classify(self, query: str) -> str:
        if self._classify:
            return await self._classify(query)
        return "medium"  # 无分类器时的安全默认

    def fallback_from(self, model_name: str) -> str | None:
        return self._cfg.fallback.get(model_name)
```

### 2.5 LLM 分类器

分类器在 `base.py:277-290` 定义为闭包，复用 system model（deepseek-v4-flash, thinking disabled, temp 0.3）：

```python
async def _classify(query: str) -> str:
    prompt = (
        "Classify this task as 'medium' or 'complex'. "
        "medium = simple chat, file I/O, single tool call. "
        "complex = multi-step reasoning, many tool calls, code generation, planning. "
        "Return ONLY one word (medium or complex).\n\n"
        f"Task: {query[:300]}"
    )
    result = (await _system_model_call(prompt)).strip().lower()
    return result if result in ("medium", "complex") else "medium"
```

输入截断至 300 字符，分类失败或异常一律返回 `"medium"` → quick，不阻塞主流程。

### 2.6 引擎集成

在 `GraphEngine` 的每次 turn 调用模型前执行路由（`graph.py:274-277`）：

```python
model = state["current_model"]
if self.model_router:
    model = await self.model_router.route(
        self._last_user_message(state), state.get("messages", [])
    )
    state["current_model"] = model
```

路由在压缩之前执行（`graph.py:280`），确保压缩使用正确模型的窗口大小。每次 turn 之间可无缝切换模型。

### 2.7 自动推导

`AdvancedConfig.auto_derive()`（`config.py:35-41`）：当 agent 配置了多个模型但未显式指定 routing 时，自动启用 two_tier 策略。

### 2.8 降级链

| 层级 | 触发条件 | 行为 |
|------|----------|------|
| LLM 分类器异常 | 模型调用失败 | 返回 `"medium"` → quick |
| classify 映射缺失 | 分类结果不在 `classify` dict 中 | 使用 `config.default` |
| default 为空 | 未配置默认模型 | 回退到 state 初始 model |
| fallback 映射 | `deep` 不可用 | `fallback_from()` 返回 `quick` |

**事实校验**：`fallback_from()` 已在 `TwoTierRouter` 中实现，但当前 `GraphEngine` 在模型调用失败时尚未自动调用它。降级链的最后一级尚未接入引擎。

### 2.9 KV Cache — 框架有意不介入

KV cache 由推理侧（DeepSeek API）在服务端管理，框架不操作缓存生命周期。理由：
- DeepSeek 缓存机制已很强，上下文复用时延迟显著降低
- 框架侧介入需感知推理引擎内部状态，增加耦合
- Token 感知的压缩策略已在前端减少了发送到 API 的上下文量

如果未来需要框架侧 KV cache 管理，可能的介入点：跨 turn 复用 system prompt 部分的 KV cache（prompt 不变时不重复编码），或多 session 间共享缓存前缀。

### 2.10 ModelAdapter — 重试与容错

`arf/core/model_adapter.py`（269 行）。统一的 OpenAI 兼容端点封装：
- **指数退避重试**：429/5xx 等瞬时错误自动重试（默认 3 次，退避基数 1.5x）
- **DeepSeek thinking 翻译**：`thinking_enabled` → `extra_body["thinking"]` 的 enabled/disabled 格式
- **流式支持**：`chat_stream_full()` 产出 `chunk`、`tool_call`、`usage`、`error` 四种事件
- **空 key 保护**：`api_key` 为空时使用 `"sk-placeholder"` 防止 OpenAI SDK 拒绝 falsy 值

### 2.11 配置

```yaml
models:
  - name: quick
    model: deepseek-v4-flash
    context_window: 800000
  - name: deep
    model: deepseek-v4-pro
    context_window: 1000000

advanced:
  routing:
    strategy: two_tier      # two_tier | static
    default: quick
    classify:
      medium: quick
      complex: deep
    fallback:
      deep: quick

  memory:
    model: quick            # system model，框架后台任务共用
    temperature: 0.3
    thinking_enabled: false
```

### 2.12 策略对比

| 策略 | 行为 | 适用场景 |
|------|------|----------|
| `two_tier` | LLM 分类器动态判断，每次 turn 可切换模型 | 主 Agent，面向用户任务 |
| `static` | 始终使用 `default`，不分类 | SysAgent，所有任务都是确定性系统操作 |

### 2.13 System Model — 框架后台任务的专用模型

框架运行时会触发一系列后台操作——记忆抽取、记忆检索、上下文压缩、路由分类。这些操作对质量要求不高（分类错了只是多用一次 deep，摘要差点也能继续对话），但对延迟和成本敏感（每次 turn 都要跑）。如果走用户模型通道，这些隐形消耗会大幅推高 token 用量。

ARF 的方案是：**框架后台任务统一由一个廉价模型实例执行**，称为 system model。它与用户任务模型共享同一个适配器池，不额外增加 API 连接。

**定义流程**（`base.py:156-183`）：

```
agent.yaml                    config.models              ModelAdapter
advanced.memory.model: quick → lookup by name → quick → _system_model_call
                                     ↓ 找不到
                              config.models[0]（回退）
                                     ↓ 为空
                              None → _system_model_call = None
```

- 从 `config.models` 中查找 `advanced.memory.model` 指定名字的模型（如 `quick`）
- 找不到则回退到 `config.models[0]`
- 用低温度（0.3）、关闭 thinking、`max_tokens=1024` 创建适配器
- 最终产物是一个 `_system_model_call(prompt: str) -> str` 闭包——输入简短 prompt，返回纯文本

**四个消费者及其退化行为**：

| 功能 | 有 system_model | 无 system_model（退化） |
|------|----------------|----------------------|
| **记忆写入** | `LLMMemoryWriter` — LLM 从最近 4 条消息抽取事实/偏好/决策，去重后写入 `memory.json` | `RuleBasedMemoryWriter` — 中文/英文关键词 → category 映射，按 content 字符串去重 |
| **记忆检索** | `LLMMemoryRetriever` — LLM 从记忆索引中筛选 top_k 条相关记忆注入 system prompt | `RecentFirstRetriever` — 按时间倒序返回最近记忆，不做语义匹配 |
| **压缩摘要** | `_summarize` — LLM 将旧轮次压缩为结构化摘要（7 个维度），追加到 `context_summary` | `_summarizer = None` — 旧消息直接丢弃，不生成摘要 |
| **路由分类** | `_classify` — LLM 判断 query 复杂度（medium / complex） | `classify()` 始终返回 `"medium"` — 全部走 quick 模型 |

所有退化都是静默的——不报错、不阻塞主流程。框架通过 `if _system_model_call:` 模式检查是否存在，不存在时回退到确定性规则方案。

**配置**：

```yaml
advanced:
  memory:
    model: quick            # 指定 system model，须与 models/ 中某个模型同名
    temperature: 0.3        # 低温度减少随机性，降低 token 消耗
    thinking_enabled: false # 后台任务不需要深度推理
```

**设计约束**：
- system model 与用户模型共享同一个 API key（`api_key_env`），不单独认证
- system model 在 BaseAgent 初始化时创建一次，生命周期与 Agent 相同
- 不存在时不报错——框架静默退化到规则方案，面向无 LLM 环境

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
