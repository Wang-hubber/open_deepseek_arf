# Memory Management — Token-Aware Context & Long-Term Persistence

ARF 将 OS 内存管理的两个核心机制——虚拟内存和文件系统——适配到 Token 时代。上下文窗口是物理内存，Token 是字节，压缩是页面置换，记忆持久化是文件系统。

---

## 1. OS 方案演进

> 本章描述 OS 如何处理内存不足与数据持久化问题，作为 ARF 设计思路的参考。非严格技术对标。

### 1.1 虚拟内存的演进

**实模式（Real Mode）** — 程序直接访问物理地址，无隔离无保护。一个程序踩坏另一个程序的内存，崩溃无法定位。

**分段（Segmentation）** — Intel 8086 引入。段基址 + 偏移量构成线性地址。代码段、数据段、堆栈段分离，提供基本隔离。问题：段大小可变导致外部碎片——总空闲够但分配不了连续段。

**分页（Paging）** — 80386 引入。物理内存切分为固定 4KB 页框，虚拟地址通过页表映射到物理页框。页表项（PTE）存储物理页号 + 权限位（present/rw/user）。解决了外部碎片：任何空闲物理页都可分配给任何虚拟页。

**虚拟内存（Demand Paging）** — 页表项的 present 位为 0 时触发缺页异常（Page Fault），内核从磁盘读入页面。这允许运行比物理内存大的程序——不是所有页都要同时在内存中。物理内存不足时，页面置换算法决定换出哪些页：

| 算法 | 思想 | 问题 |
|------|------|------|
| FIFO | 换出最老页面 | Belady 异常：更多内存反而更多缺页 |
| LRU | 换出最久未用页面 | 每次访问需更新时间戳，硬件开销大 |
| CLOCK（二次机会） | 环形链表 + 访问位，近似 LRU | 现代 OS 标准方案 |

**Linux 的 LRU 双链表** — 页面分为 active（最近被访问）和 inactive（最近未访问）两个链表。kswapd 守护进程在水位线低于 low 时异步回收 inactive 页面；低于 min 时同步直接回收。OOM Killer 作为最后手段，按进程 oom_score 选择杀死。

**多级页表** — 单级页表随地址空间增长而膨胀（64 位地址需要 512GB 页表）。x86-64 用 4 级页表（PGD→PUD→PMD→PTE），每级 512 项，稀疏地址空间只需部分页表驻留。

### 1.2 文件系统与持久化

**文件系统** — 提供命名、组织、持久化存储。VFS（Virtual File System）抽象层统一不同文件系统的操作接口。

**mmap** — 将文件映射到进程虚拟地址空间。访问映射区域触发缺页，内核从 page cache 读入。写入可回写磁盘。关键洞察：**page cache 统一了内存和文件 IO**——文件数据缓存和匿名内存页面在同一个 LRU 链表中管理。

### 1.3 对 ARF 的启发

OS 用虚拟内存解决"内存不够"的思路——换出不常用的页面、需要时换入——直接影响了 ARF 的压缩策略。OS 用多级页表管理稀疏地址空间的思路，影响了记忆索引的演进方向。OS 用 mmap 统一内存与文件的思路，影响了工具输出摘要的设计。

---

## 2. ARF 当前实现

内存管理分为两条管道：**压缩管道**（上下文管理，防 OOM）和**记忆管道**（长期持久化）。两条管道共享 `context_summary` 载体，在引擎循环中协同工作。

### 2.1 架构总览

```
每个用户交互轮次
    │
    ├─ [1] 记忆检索 — LLMMemoryRetriever
    │       从 memory.json 加载 → 语义筛选 top_k → 注入 context_summary
    │
    ├─ [2] 模型路由 — TwoTierRouter
    │       选定模型 → 获取该模型的 context_window
    │
    ├─ [3] 压缩判断 — SlidingWindowCompactor.should_compact()
    │       last_token_usage > 0.75 × context_window ?
    │       ├─ No  → 继续
    │       └─ Yes → compact()
    │                  ├─ 保留最近 4 条消息
    │                  ├─ 旧消息 → LLM 结构化摘要 → 追加到 context_summary
    │                  └─ 返回精简 state
    │
    ├─ [4] 模型调用 → 响应 + usage.total_tokens
    │
    ├─ [5] 工具输出摘要 — summarize_tool_output()
    │       长输出（>2000 chars）→ 写 disk + LLM 摘要 → 上下文保留摘要指针
    │
    └─ [6] 记忆抽取 — LLMMemoryWriter.extract_and_write()
            从最近 4 条消息提取事实/偏好/决策 → 去重 → 写入 memory.json
```

### 2.2 压缩管道

**文件**：`arf/compaction/sliding_window.py`，`arf/agent/base.py`（summarizer 闭包）

**触发机制**（`sliding_window.py`）：以上一轮模型调用返回的 `usage.total_tokens` 为信号，在下一轮模型调用之前判断。区别于直接统计 `len(messages)`，API 报告的 token 用量包含工具定义、system prompt 等隐形消耗，更准确。

```python
def should_compact(self, state, threshold=0.75, window_size=None):
    w = window_size or self._window_size  # 默认 131,072
    last_usage = state.get("last_token_usage", 0)
    return last_usage > threshold * w
```

**窗口跟随模型**：路由先于压缩执行（`graph.py`）。不同模型的 `context_window` 不同（如 flash 800k, pro 1M），引擎将当前选定模型的窗口大小传入 `should_compact`。切换到更小窗口的模型时，阈值自动收紧。

**压缩行为**（`sliding_window.py`）：

- 保留最近 4 条消息（一个用户-助手往返 + 工具调用）
- 旧消息通过 LLM 生成结构化摘要，追加 `[Earlier]` 标记到 `context_summary`
- 摘要叠加而非覆盖：连续多轮压缩时，每轮生成的摘要累积保留，避免历史信息丢失
- 失败静默降级：summarizer 调用异常时仅记录日志，丢弃旧消息继续执行

**LLM Summarizer**（`base.py`）：复用框架的 system model（deepseek-v4-flash, thinking disabled, temp 0.3）。取最近 30 条旧消息，每条截断至 300 字符。结构化输出包含：Completed / In Progress / Files Modified / Decisions / Facts & Preferences / Errors & Debugging / Next Steps 七个部分。

**工具输出摘要**（`sliding_window.py`）：工具输出超过 2000 字符时，原文写入 `memory/tool_outputs/turn_{N}_{tool_name}.txt`，上下文保留 LLM 摘要 + 文件路径指针。短输出原样保留。类似 mmap 的思路——大文件不需要全部读入内存，按需映射即可。

### 2.3 记忆管道

**协议层**（`arf/core/protocols/memory.py`）：

| 协议 | 职责 | 关键方法 |
|------|------|----------|
| `MemoryStore` | 持久化 CRUD | `save(entry)`, `load(session_id)`, `delete(entry_id)` |
| `MemoryRetriever` | 语义检索 | `retrieve(store, query, session_id, max_tokens, top_k)` |
| `MemoryWriter` | 自动抽取 | `extract_and_write(store, turn_messages, existing_entries)` |

**MemoryEntry**（`core/protocols/memory.py`）：`id`（UUID）、`content`（记忆内容 ≤500 chars）、`category`（fact/preference/decision/context）、`timestamp`、`source_turn`、`relevance_score`、`replaces`（更新链）。

**FileMemoryStore**（`arf/memory/file_store.py`）：
- 单文件 `memory/memory.json`，所有条目 JSON 序列化
- `save()` 按 id 覆盖；`delete()` 按 id 移除
- O(n) 扫描，无索引。数百条规模内可接受

**LLMMemoryWriter**（`arf/memory/llm_writer.py`）：
- 每 turn 结束后异步调用（`graph.py`），输入最近 4 条消息 + 已有记忆索引
- LLM 返回 `{"actions": [{"action": "add|update|delete", "entry": {...}, "replaces": "old-id"}]}`
- 去重由 LLM 判断——对比已有记忆索引，能修正、精炼或否定之前的记忆
- JSON 解析失败时跳过该 turn，保留已有记忆。`_parse_json_response()` 支持 markdown 围栏、双花括号、截取外层 `{}` 等常见 LLM 输出格式

**LLMMemoryRetriever**（`arf/memory/llm_retriever.py`）：
- 每 turn 开始前调用（`graph.py`），输入用户消息 + 记忆摘要索引（id + category + 前 120 字符）
- LLM 返回 `{"relevant_ids": [...]}`
- 结果按 max_tokens 截断（chars/3 ≈ tokens），不超出 system prompt 预算
- 回退链：JSON 解析失败 → RecentFirstRetriever；LLM 调用异常 → RecentFirstRetriever

**RuleBasedMemoryWriter**（`arf/memory/writer.py`）：
- 无 LLM 依赖的轻量替代，中英文关键词 → category 映射
- 仅匹配 assistant 消息，最多 500 字符，按 content 字符串去重

### 2.4 引擎集成

在 `GraphEngine.invoke()` 和 `astream()` 中（`graph.py`），记忆系统在以下节点介入：

1. **检索** — 每 turn 循环开始时，在路由之前：`memory_retriever.retrieve()` → 注入 `state["context_summary"]`
2. **压缩** — 路由之后、模型调用之前：`compaction.should_compact()` → `compaction.compact()`
3. **工具输出摘要** — 工具执行成功后：`compaction.summarize_tool_output()`
4. **记忆写入** — 两条路径均覆盖：文本响应路径（invoke:352-358, astream:628-632）和工具执行路径（invoke:469-475, astream:721-727）。写入前必先 `store.load(session_id)` 获取最新条目，避免并发写入导致陈旧覆盖

### 2.5 配置

```yaml
advanced:
  compaction:
    strategy: sliding_window    # sliding_window | none
    threshold: 0.75             # 触发比例，按选定模型的 context_window 计算

  memory:
    store: file                 # file | sqlite | none
    workspace: ./memory
    retriever: llm              # llm | recent_first
    writer: llm                 # llm | rule
    max_tokens: 2000
    top_k: 5
    model: quick                # 用廉价模型做记忆操作
    temperature: 0.3
    thinking_enabled: false
```

---

## 3. 演进方向

### 3.1 语义单元检索

当前检索粒度为整条 MemoryEntry（最长 500 字符），通过 LLM 从全量索引中挑选 top_k 条。一条记忆可能包含多个独立事实，其中只有半句与当前 query 相关，但整条记忆都被注入 system prompt——粒度偏粗。

参考 OS 分页的思路（将内存切为固定 4KB 页面，缺页时只换入所需页面而非整个段），将记忆切分为更细粒度的**语义单元**（原子事实，类似 RDF 三元组）。检索时按语义单元匹配，注入时只包含精确命中的单元。

具体步骤：
1. LLMMemoryWriter 拆分：先抽取原子事实，再按语义关系聚类写入
2. LLMMemoryRetriever 匹配：在语义单元级做相关性判断（可结合 embedding 向量）
3. 注入时只包含匹配的语义单元，token 效率显著提升

### 3.2 知识图谱索引

当前 FileMemoryStore 是扁平列表，检索靠 LLM 扫描全量索引（O(n)）。记忆条目增长到数千条时，每次检索都需 LLM 遍历所有条目的摘要，成为瓶颈。

参考 OS 多级页表将查找从 O(n) 降到 O(log n) 的思路：
1. **分类索引**：按 category 建立一级索引，检索时先按类筛选
2. **关系边**：LLMMemoryWriter 同时抽取条目间关系（`related_to`、`contradicts`、`elaborates`），写入时建立图结构
3. **图遍历检索**：从最相关的种子条目出发，沿关系边扩展一跳邻居，优先注入有关联的记忆
4. **存储后端**：SQLite 替代 JSON，支持按 category 和时间范围查询

### 3.3 探索性方向

**主动预取**：根据任务模式预测下一步需要的记忆。用户连续操作同一文件时，预加载该文件相关的所有记忆。

**冷热分离**：高频记忆（最近 5 轮内引用）直接注入 system prompt；中频通过检索注入；低频仅在显式匹配时加载。比当前"所有检索结果一律注入"更精细地控制 token 预算。

**记忆衰减**：记忆条目携带最后访问时间戳。长期未被检索的记忆自动降级到冷存储，不参与常规检索。被 LLM 标记 delete/update 的记忆进入墓碑期，软删除 N 轮后再物理删除。

**压缩与记忆融合**：当前压缩产生的 `[Earlier]` 摘要和记忆检索结果都在 `context_summary` 中，但独立管理。可将压缩摘要也输入 LLMMemoryWriter 进行结构化抽取，让历史摘要进入记忆系统长期保存，而非仅存于当前会话的上下文。
