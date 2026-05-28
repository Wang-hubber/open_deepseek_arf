# Context Management — Token-Aware Context Window Compaction

ARF 将 OS 虚拟内存的页面置换机制适配到 Token 时代。上下文窗口是物理内存，Token 是字节，压缩是页面置换。当上下文接近窗口上限时，旧轮次被压缩为结构化摘要，释放空间给新对话。

> **长期记忆**（跨会话事实提取）已移至 [`arf/plugins/memory/`](plugins/memory.md) 插件。本文档仅涵盖会话内上下文压缩。

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

---

## 2. ARF 当前实现

上下文管理由 `SlidingWindowCompactor` 驱动，在引擎循环中与其他子系统协同工作。

### 2.1 架构总览

```
每个用户交互轮次
    │
    ├─ [1] 路由 — TwoTierRouter 选定模型 → 获取该模型的 context_window
    │
    ├─ [2] 压缩判断 — SlidingWindowCompactor.should_compact()
    │       last_token_usage > threshold × context_window ?
    │       ├─ No  → 继续
    │       └─ Yes → compact()
    │                  ├─ 保留最近 4 条消息
    │                  ├─ 旧消息 → LLM 结构化摘要 → 追加到 context_summary
    │                  └─ 返回精简 state
    │
    ├─ [3] 模型调用 → 响应 + usage.total_tokens
    │
    └─ [4] 工具输出摘要 — summarize_tool_output()
            长输出（>2000 chars）→ 写 disk + LLM 摘要 → 上下文保留摘要指针
```

### 2.2 触发机制

**文件**：`arf/compaction/sliding_window.py`

以上一轮模型调用返回的 `usage.total_tokens` 为信号，在下一轮模型调用之前判断。区别于直接统计 `len(messages)`，API 报告的 token 用量包含工具定义、system prompt 等隐形消耗，更准确。

```python
def should_compact(self, state, window_size=None):
    w = window_size or self._window_size  # 默认 131,072
    last_usage = state.get("last_token_usage", 0)
    return last_usage > self._threshold * w
```

### 2.3 窗口跟随模型

路由先于压缩执行（`graph.py`）。不同模型的 `context_window` 不同（如 flash 800k, pro 1M），引擎将当前选定模型的窗口大小传入 `should_compact`。切换到更小窗口的模型时，阈值自动收紧。

### 2.4 压缩行为

- 保留最近 4 条消息（一个用户-助手往返 + 工具调用）
- 旧消息通过 LLM 生成结构化摘要，追加 `[Earlier]` 标记到 `context_summary`
- 摘要叠加而非覆盖：连续多轮压缩时，每轮生成的摘要累积保留，避免历史信息丢失
- 失败静默降级：summarizer 调用异常时仅记录日志，丢弃旧消息继续执行

### 2.5 LLM Summarizer

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

### 2.6 工具输出摘要

工具输出超过 2000 字符时，原文写入 `memory/tool_outputs/turn_{N}_{tool_name}.txt`，上下文保留 LLM 摘要 + 文件路径指针。短输出原样保留。类似 mmap 的思路——大文件不需要全部读入上下文，按需映射即可。

### 2.7 引擎集成

在 `GraphEngine.invoke()` 和 `astream()` 中：

1. **路由之后、模型调用之前**：`compaction.should_compact()` → `compaction.compact()`
2. **工具执行成功后**：`compaction.summarize_tool_output()`

路由先于压缩执行，确保使用正确模型的窗口大小。

### 2.8 配置

```yaml
advanced:
  compaction:
    strategy: sliding_window    # sliding_window | none
    threshold: 0.75             # 触发比例，按选定模型的 context_window 计算

  system_model: quick           # 系统后台模型，低温度、关闭推理
```

---

## 3. 演进方向

### 3.1 语义单元压缩

当前压缩粒度为整条消息，通过 LLM 生成自然语言摘要。可参考 OS 分页的思路，将消息切分为更细粒度的语义单元，按重要性分级保留：关键决策→原文保留，过渡性对话→关键词索引，调试信息→丢弃。

### 3.2 自适应阈值

当前阈值是固定比例（75%）。可参考 Linux kswapd 的水位线机制，引入三级阈值：
- **low（70%）**：异步压缩，不阻塞当前 turn
- **min（85%）**：同步压缩，压缩完成后继续
- **critical（95%）**：激进压缩，仅保留最近 2 条消息

### 3.3 跨会话摘要复用

当前 `context_summary` 仅在当前会话内有效。后续可将压缩摘要作为输入供 memory 插件参考，让长期记忆提取受益于会话内的结构化摘要。类似 OS 的 page cache——压缩摘要可跨会话复用，避免重复处理相同上下文。

### 3.4 探索性方向

**选择性保留**：当前"保留最近 4 条"是固定窗口。可以改为按消息重要性保留——模型标记哪些消息对后续推理关键，压缩时优先保留关键消息而非简单按时间。

**压缩预算协商**：当 memory 插件需要更多 token 注入长期记忆时，与压缩器协商 system prompt 预算分配。类似 OS 的 cgroup 内存配额。
