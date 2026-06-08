# Context Management — Token-Aware Context Window Compaction

ARF 将 OS 虚拟内存的页面置换机制适配到 Token 时代。上下文窗口是物理内存，Token 是字节，压缩是页面置换。当上下文接近窗口上限时，旧轮次被压缩为结构化摘要，释放空间给新对话。

> **长期记忆**（跨会话事实提取）通过 `arf/plugins/memory/` 插件实现。使用 `round_end` hook 每 N 轮触发 LLM 提取用户身份、偏好、决策等持久事实，写入 `memory/memory.md`。本文档涵盖会话内上下文压缩。

---

## 1. OS 方案演进

> 本章描述 OS 如何处理内存不足问题，作为 ARF 压缩策略的参考。非严格技术对标。

### 1.1 虚拟内存与页面置换

**实模式（Real Mode）** — 程序直接访问物理地址，无隔离无保护。一个程序踩坏另一个程序的内存，崩溃无法定位。

**分段（Segmentation）** — Intel 8086 引入。段基址 + 偏移量构成线性地址。代码段、数据段、堆栈段分离，提供基本隔离。问题：段大小可变导致外部碎片。

**分页（Paging）** — 80386 引入。物理内存切分为固定 4KB 页框，虚拟地址通过页表映射到物理页框。页表项（PTE）存储物理页号 + 权限位（present/rw/user）。解决了外部碎片。

**虚拟内存（Demand Paging）** — 页表项的 present 位为 0 时触发缺页异常（Page Fault），内核从磁盘读入页面。允许运行比物理内存大的程序。物理内存不足时，页面置换算法决定换出哪些页：

| 算法 | 思想 | 问题 |
|------|------|------|
| FIFO | 换出最老页面 | Belady 异常：更多内存反而更多缺页 |
| LRU | 换出最久未用页面 | 每次访问需更新时间戳，硬件开销大 |
| CLOCK（二次机会） | 环形链表 + 访问位，近似 LRU | 现代 OS 标准方案 |

**Linux 的 LRU 双链表** — 页面分为 active（最近被访问）和 inactive（最近未访问）两个链表。kswapd 守护进程在水位线低于 low 时异步回收 inactive 页面；低于 min 时同步直接回收。

### 1.2 mmap 与 Page Cache

**mmap** — 将文件映射到进程虚拟地址空间。访问映射区域触发缺页，内核从 page cache 读入。关键洞察：**page cache 统一了内存和文件 IO**——文件数据缓存和匿名内存页面在同一个 LRU 链表中管理。

### 1.3 对 ARF 的启发

OS 用虚拟内存解决"内存不够"的思路——换出不常用的页面、需要时换入——直接影响了 ARF 的压缩策略。mmap 统一内存与文件的思路，影响了工具输出摘要的设计：大文件不需要全部读入上下文，按需映射即可。

**OS → ARF 概念映射**：

| OS 概念 | ARF 映射 | 说明 |
|---------|----------|------|
| PTE present 位 → Page Fault | `should_compact()` — 当 `last_token_usage > threshold × window` 触发压缩 | 缺页类比令牌压力，触发置换 |
| kswapd 水位线 (low/min) | threshold 0.75 — 低于水位线压缩，低于 min 激进压缩 | Linux 异步回收 inactive 页面 → 压缩旧轮次 |
| LRU 双链表 (active/inactive) | context_summary (摘要) + messages[-N:] (活跃) | 冷热分离：旧消息压缩进摘要，最近消息保持原文 |
| mmap + Page Cache | `summarize_tool_output()` — 长输出写 disk，上下文留摘要指针 | 不全部加载，按需映射 |

---

## 2. ARF 当前实现

上下文管理由 `SlidingWindowCompactor` 驱动，在引擎循环中与其他子系统协同工作。

### 2.1 架构总览

```
每个用户交互轮次
    │
    ├─ [1] 压缩判断 — SlidingWindowCompactor.should_compact()
    │       last_token_usage > threshold × context_window ?
    │       ├─ No  → 继续
    │       └─ Yes → compact()
    │                  ├─ 过滤 tool 消息（仅保留 user/assistant）
    │                  ├─ 保留最近 N 条 user/assistant 消息
    │                  ├─ 旧消息 → LLM 结构化摘要 → 追加到 context_summary
    │                  └─ 返回精简 state
    │
    ├─ [2] 模型调用 → 响应 + usage.total_tokens
    │
    └─ [3] 工具输出摘要 — summarize_tool_output()
            长输出（>2000 chars）→ 写 disk + LLM 摘要 → 上下文保留摘要指针
```

### 2.2 触发机制

**文件**：`arf/compaction/sliding_window.py`

以上一轮模型调用返回的 `usage.total_tokens` 为信号，在下一轮模型调用之前判断。区别于直接统计 `len(messages)`，API 报告的 token 用量包含工具定义、system prompt 等隐形消耗，更准确。

默认窗口大小为模块常量 `DEFAULT_WINDOW_SIZE = 131_072`（DeepSeek V4 标准上下文窗口）：

```python
def should_compact(self, state, window_size=None):
    w = window_size or self._window_size  # 默认 DEFAULT_WINDOW_SIZE (131,072)
    last_usage = state.get("last_token_usage", 0)
    return last_usage > self._threshold * w
```

默认压缩比为 75%（`threshold=0.75`），即 token 用量超过窗口的 75% 时触发。压缩后跳过 2 轮冷却期（`_compaction_cooldown`），避免大轮次的 residual token usage 导致假重触发。

### 2.3 协议

`CompactionStrategy` 协议（`arf/core/protocols/compaction.py`）：

```python
class CompactionStrategy(Protocol):
    def should_compact(self, state: AgentState, threshold: float = 0.75) -> bool: ...
    async def compact(self, state: AgentState) -> AgentState: ...
```

`SlidingWindowCompactor` 是该协议的唯一实现。将协议与实现分离，允许未来注入替代压缩策略（如语义单元压缩），引擎无需修改。

### 2.4 窗口跟随模型

引擎在每轮 turn 结束时将 `usage.total_tokens` 写入 state。不同模型的 `context_window` 由模型适配器配置携带（`ModelAdapter.context_window`），引擎通过模型窗口信息传入 `should_compact`。切换到更小窗口的模型时，阈值自动收紧。

当模型窗口信息未设置时，引擎回退到 `DEFAULT_WINDOW_SIZE`（131_072）。

### 2.5 压缩行为

- **消息过滤**：压缩时保留最近 N 条 user/assistant 消息及其关联的 tool 消息。旧消息中的 tool 消息（工具调用结果）被视为瞬时上下文被丢弃，因为 tool message 必须紧跟其对应的 assistant(tool_calls)，压缩截断后产生孤儿消息导致 API 400
- 默认保留最近 8 条 user/assistant 消息（`keep_count: int = 8`，可通过构造函数配置）
- 旧消息通过 LLM 生成结构化摘要，追加 `[Earlier]` 标记到 `context_summary`
- 摘要叠加而非覆盖：连续多轮压缩时，每轮生成的摘要累积保留，避免历史信息丢失
- 失败静默降级：summarizer 调用异常时仅记录日志，丢弃旧消息继续执行

### 2.6 LLM Summarizer

在 `BaseAgent.__init__()` 中定义为闭包，复用 system model（deepseek-v4-flash, thinking disabled, temp 0.3）。取最近 30 条旧消息，每条截断至 300 字符。结构化输出包含：Completed / In Progress / Files Modified / Decisions / Facts & Preferences / Errors & Debugging / Next Steps 七个部分。

```python
async def _summarize(msgs: list[dict]) -> str:
    text = "\n".join(
        f"[{m.get('role', '?')}] {m.get('content', '')[:300]}"
        for m in msgs[-30:]
    )
    prompt = (
        "You are compacting conversation history to free context space.\n"
        "Write a structured summary that preserves the essential state:\n\n"
        "<conversation>\n{text}\n</conversation>\n\n"
        "Output a concise summary with these sections (omit empty ones):\n"
        "- Completed: tasks finished, problems solved\n"
        "- In Progress: current task, remaining TODO items\n"
        "- Files Modified: paths and what was changed\n"
        "- Decisions: architectural choices, agreed approaches\n"
        "- Facts & Preferences: user info, likes/dislikes, constraints\n"
        "- Errors & Debugging: error messages, stack traces, hypotheses\n"
        "- Next Steps: what should happen next"
    ).replace("{text}", text)
    return (await _system_model_call(prompt)).strip()
```

### 2.7 工具输出摘要

工具输出超过 2000 字符时，原文写入 `workspace/tool_outputs/turn_{N}_{tool_name}.txt`，上下文保留 LLM 摘要 + 文件路径指针。短输出原样保留。类似 mmap 的思路——大文件不需要全部读入上下文，按需映射即可。

无 system model 时静默退化：长输出仅截断保留前 2000 字符 + 磁盘路径（`[Tool output truncated — full at ...]`），不生成 LLM 摘要。退化路径与压缩摘要一致：`if self._summarize:` 模式检查，不存在时跳过 LLM 调用。

### 2.8 引擎集成

压缩逻辑在 `ControlPlane` 的 `_dispatch` 循环中触发，位置在模型调用之前。引擎通过 `if self.compaction:` 守卫检查压缩器是否存在——未配置压缩时跳过所有压缩流程。

### 2.9 配置

```yaml
advanced:
  compaction:
    strategy: sliding_window    # sliding_window | none
    threshold: 0.75             # 触发比例，按选定模型的 context_window 计算

  system_model: quick           # 系统后台模型，低温度、关闭推理
```

- **`sliding_window`**：默认策略，LLM 驱动压缩（需 `system_model` 提供摘要能力，无 system_model 时静默退化到截断）
- **`none`**：完全禁用压缩——消息无限增长不做处理，适用于短会话或调试场景。不创建 `SlidingWindowCompactor` 实例

---

## 3. 演进方向

### 3.1 语义单元压缩

当前压缩粒度为整条消息，通过 LLM 生成自然语言摘要。可参考 OS 分页的思路，将消息切分为更细粒度的语义单元，按重要性分级保留：关键决策→原文保留，过渡性对话→关键词索引，调试信息→丢弃。

### 3.2 自适应阈值

当前阈值是固定比例（75%）。可参考 Linux kswapd 的水位线机制，引入三级阈值：
- **low（70%）**：异步压缩，不阻塞当前 turn
- **min（85%）**：同步压缩，压缩完成后继续
- **critical（95%）**：激进压缩，仅保留最近 2 条消息

### 3.3 长期记忆提取 (Memory Plugin)

通过 `arf/plugins/memory/` 插件的 `round_end` hook 实现。每轮交互结束后，hook 子进程检查 `interaction_round % interval == 0`（默认 interval=10，由 `config.yaml` 配置），触发时将最近 20 条消息交给 system model 提取持久事实，写入 `memory/memory.md`。

**提取规则**：
- 用户身份（角色、技能、背景）
- 工作偏好（语言、风格、工具）
- 决策与原因（架构、技术栈、拒绝的方案）
- 跨会话事实（项目布局、部署流程、认证方式）

**注入机制**：框架通过 `PluginRuntime` 对象向 hook 子进程注入运行环境——API keys、模型配置、应用路径、会话上下文——序列化为 `ARF_RUNTIME` JSON 环境变量。Hook 脚本一行 `json.loads(os.environ["ARF_RUNTIME"])` 获取全部上下文。

**Trigger chain**: `ControlPlane.round_end → hook_runner.fire("round_end") → ARF_RUNTIME env → round_end.py → subprocess.run(extractor.py) → system model → memory.md`

参见 [`arf/plugins/memory/`](../../arf/plugins/memory/) 和 [`PluginRuntime`](../../arf/core/plugin_runtime.py)。

### 3.4 跨会话摘要复用

当前 `context_summary` 仅在当前会话内有效。后续可将压缩摘要作为输入供 memory 插件参考，让长期记忆提取受益于会话内的结构化摘要。类似 OS 的 page cache——压缩摘要可跨会话复用，避免重复处理相同上下文。

### 3.4 会话外压缩 (Off-Session Compaction)

当前压缩均为会话内：必须在模型调用前同步完成，受限于时间和算力（廉价 system model、最近 30 条消息窗口）。会话外压缩在会话非活跃时异步执行，可以回溯完整会话、配置更强模型、产出更结构化的记忆。

**触发机制**（可组合）：

| 模式 | 触发条件 |
|------|----------|
| 用户触发 | 会话显式结束（/stop、关闭） |
| 定时扫描 | 周期性扫描非活跃会话，逐个处理 |

**核心策略：父子节点摘要**（参照 RAG 父子检索）

1. 回溯完整会话 → 按主题切分段落
2. 每个主题生成父子对：**父节点**（主题摘要，注入上下文）→ **子节点**（原始对话细节，存盘保留）
3. 上下文仅加载父节点 + 子节点磁盘路径，按需检索

**与会话内压缩的对比**：

| 维度 | 会话内 | 会话外 |
|------|--------|--------|
| 窗口 | 最近 N 条消息 | 完整会话回溯 |
| 模型 | system model (flash) | 可配置 |
| 时机 | 同步（模型调用前） | 异步（会话非活跃） |
| 粒度 | 固定 7 维模板 | 主题驱动的父子分层 |
| 输出 | `context_summary` 字符串 | 结构化父子节点文件 |

### 3.5 探索性方向

**选择性保留**：当前"保留最近 N 条"是固定窗口。可以改为按消息重要性保留——模型标记哪些消息对后续推理关键，压缩时优先保留关键消息而非简单按时间。

**压缩预算协商**：当 memory 插件需要更多 token 注入长期记忆时，与压缩器协商 system prompt 预算分配。类似 OS 的 cgroup 内存配额。
