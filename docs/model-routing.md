# Model Routing — Fast/Slow Dispatch & Resource Allocation

ARF 将模型调度拆成两条路径：**用户任务**走复杂度路由（快慢分流），**框架任务**走专用模型（固定分配）。两者共享同一套分类器和路由基础设施。

## Architecture

```
用户消息进入
    │
    ▼
TwoTierRouter.classify(query)
    │  LLM 分类器（复用 memory model）
    │  返回 "medium" 或 "complex"
    │
    ├─ medium  → quick  (deepseek-v4-flash)
    │            简单聊天、单工具调用、文件 I/O
    │
    └─ complex → deep   (deepseek-v4-pro)
                 多步推理、代码生成、规划任务

框架后台任务（记忆抽取、检索、分类）
    │  不走 TwoTierRouter
    │  直接使用 memory.model（配置为 quick）
    │  thinking_enabled: false, temperature: 0.3
    ▼
专用模型适配器（_mem_model_call）
```

## 两层调度

| 调度层 | 负责 | 模型选择方式 | 配置 |
|--------|------|-------------|------|
| **用户任务路由** | 每 turn 根据用户 query 复杂度选择模型 | `TwoTierRouter` + LLM 分类器 | `advanced.routing` |
| **框架任务分配** | 记忆抽取、检索、上下文压缩等后台操作 | 固定使用 `memory.model` | `advanced.memory.model` |

## 协议

`ModelRouter` 协议定义在 `arf/core/protocols/routing.py`：

```python
class ModelRouter(Protocol):
    async def route(self, query: str, history: list[dict]) -> str: ...
    async def classify(self, query: str) -> str: ...
    def fallback_from(self, model_name: str) -> str | None: ...
```

## 实现：TwoTierRouter

`arf/routing/two_tier.py` — 内置的二级路由实现：

```python
class TwoTierRouter:
    def __init__(self, config: RoutingConfig, models: list[str], classifier_call=None):
        ...

    async def route(self, query: str, history: list[dict]) -> str:
        level = await self.classify(query)          # "medium" | "complex"
        return self._cfg.classify.get(level, self._cfg.default)

    async def classify(self, query: str) -> str:
        if self._classify:
            return await self._classify(query)      # LLM 分类
        return "medium"                             # 无分类器时默认

    def fallback_from(self, model_name: str) -> str | None:
        return self._cfg.fallback.get(model_name)   # 降级链
```

### LLM 分类器

分类器复用框架的 memory model（通常为 `quick`），不需要额外模型实例：

```python
async def _classify(query: str) -> str:
    prompt = (
        "Classify this task as 'medium' or 'complex'. "
        "medium = simple chat, file I/O, single tool call. "
        "complex = multi-step reasoning, many tool calls, code generation, planning. "
        "Return ONLY one word (medium or complex).\n\n"
        f"Task: {query[:300]}"
    )
    result = (await _mem_model_call(prompt)).strip().lower()
    return result if result in ("medium", "complex") else "medium"
```

- 失败时返回 `"medium"`（安全默认）
- 输入截断至 300 字符
- 使用 `deepseek-v4-flash`（thinking disabled, temp 0.3）

## 配置

用户任务路由在 `agent.yaml` 的 `advanced.routing` 段：

```yaml
advanced:
  routing:
    strategy: two_tier      # two_tier | static
    default: quick          # 分类未命中时的默认模型
    classify:
      medium: quick         # 简单任务 → deepseek-v4-flash
      complex: deep         # 复杂任务 → deepseek-v4-pro
    fallback:
      deep: quick           # deep 不可用时降级到 quick
```

框架任务分配在 `advanced.memory` 段：

```yaml
advanced:
  memory:
    model: quick            # 记忆操作专用模型
    temperature: 0.3
    thinking_enabled: false
```

### 策略对比

| 策略 | 行为 | 适用场景 |
|------|------|----------|
| `two_tier` | LLM 分类器动态判断，每次 turn 可切换模型 | 主 Agent，用户任务 |
| `static` | 始终使用 `default`，不分类 | SysAgent（所有任务都是系统操作） |

## 引擎集成

`GraphEngine` 在每次 turn 调用模型前执行路由（`graph.py:151-152`）：

```python
if self.model_router:
    model = await self.model_router.route(
        self._last_user_message(state), state.get("messages", [])
    )
    state["current_model"] = model
```

`state["current_model"]` 被更新后，立即用于 `_call_model(msgs, model, ...)`。模型可以在每次 turn 之间无缝切换。

## 自动推导

当 agent 配置了多个模型但未显式指定 routing 时，`AdvancedConfig.auto_derive()` 自动启用路由：

```python
if models_count > 1:
    adv.routing = RoutingConfig(strategy="two_tier")
```

用户需要在 YAML 中补全 `classify` 映射。

## 降级链

| 层级 | 触发条件 | 行为 |
|------|----------|------|
| LLM 分类器异常 | 模型调用失败 | 返回 `"medium"` |
| classify 映射缺失 | 分类结果不在 `classify` dict 中 | 返回 `config.default` |
| default 为空 | 未配置默认模型 | 返回 `""`（回退到初始 model） |
| fallback 映射 | `deep` 模型不可用 | `fallback_from()` 返回 `quick` |

> **注意**：`fallback_from()` 已在协议和实现中定义，但当前引擎在模型调用失败时尚未自动调用它。这是已知的待完善点。

## 验证

简单 query（"say hi"）→ `quick`，复杂 query（"implement Dijkstra"）→ `deep`：

```
Turn 10: model=quick   ← "say hi"
Turn 11: model=deep    ← "implement Dijkstra algorithm"
Turn 12: model=deep    ← (multi-turn continuation)
```

## 与 OS 模式的对应

| OS 概念 | ARF 实现 |
|---------|----------|
| 多级缓存 (L1/L2) | 二级分类器 (quick/deep) |
| CPU 分支预测 | LLM 任务复杂度分类 |
| big.LITTLE 调度 | quick (小核) / deep (大核) |
| 协处理器 | memory model 处理框架后台任务 |
| 降级链 | classify → default → state initial |
