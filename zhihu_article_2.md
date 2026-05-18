# DeepSeek V4 上下文 1M 也不够用？ARF 的自动压缩与渐进式披露方案

> 当 Agent 会话动辄消耗几十万 token，如何让 1M 上下文窗口撑得住长会话？滑动窗口摘要 + 工具结果渐进式披露的完整实现。

---

## 引言：1M 上下文窗口的"甜蜜陷阱"

DeepSeek V4 发布后，1M token 上下文窗口成了最大卖点。对 Agent 应用来说，这既是解放也是陷阱。

**解放**：可以塞进更长的历史、更多的工具结果、更大的文件内容。

**陷阱**：Agent 的典型使用模式是一轮又一轮的对话循环——每轮可能调 3-5 次工具，每次工具返回几百到几万字，每轮 LLM 响应可能触发思维链推理。在深度开发场景中，一两个小时的编程会话轻松突破 500K token，而超过 75% 利用率后，API 延迟和成本都会急剧上升。

本文介绍 ARF 框架中的**上下文压缩**和**渐进式工具结果披露**——一个在 1M 窗口内实现"自动管理上下文"的完整方案。

---

## 一、问题建模：Agent 上下文的三个膨胀源

先分析 Agent 上下文的 token 消耗来自哪里：

| 膨胀源 | 占比（典型长会话） | 可控性 |
|--------|-------------------|--------|
| 系统提示词 + 工具 schema | ~2-3K token | ✅ 固定 |
| 近期对话（最近 3 轮） | ~5-15K token | 部分可控 |
| 早期对话历史 | ~50-200K token | ✅ 可压缩 |
| 工具调用结果 | ~20-300K token | ✅ 可截断 |
| LLM 思维链 + 响应 | ~10-50K token | ❌ 不可控 |

早期对话历史可以压缩，工具结果可以截断——这两块加起来占了大部分消耗。ARF 的方案就是对这两块分别治理。

---

## 二、上下文压缩：滑动窗口 + 结构化摘要

### 2.1 触发时机

不是定时触发，不是手动触发，而是**每次 call_model 前检查一次**。ARF 在 LangGraph 图中插入了一个 `compact` 节点：

```
classify → compact → call_model → execute_tools → compact → call_model → ...
```

每次模型调用前，紧凑节点估算当前消息列表的 token 使用量。如果低于窗口的 75%，直接透传——几乎零开销。如果超过阈值，触发压缩。

### 2.2 "保留最近 3 轮，旧消息打包摘要"

压缩策略有一个核心的设计权衡：如果摘要太多，会丢失细节；如果保留太多，窗口还是会爆。ARF 选择了一个务实的平衡点：

- **保留最近 3 轮对话完整原文**（6 条消息：3 user + 3 assistant，加上关联的 tool 消息）
- **更早的对话打包送给 `quick_no_thinking` 模型生成结构化摘要**
- 摘要以系统消息形式注入，替换掉旧消息

压缩模型选择 `quick_no_thinking`（不用推理能力，快速便宜），压缩提示词是中文的：

```
你是一个对话摘要助手。请将以下对话历史压缩为一个结构化摘要。

要求：
1. 保留关键决策、重要事实和未完成的任务
2. 合并重复信息，删除闲聊和无意义回复
3. 保留所有文件路径、代码片段和工具调用结果的关键信息
4. 输出为 Markdown 格式，不超过 2000 字
```

**每轮只压缩一次**——`has_attempted_compact` 标志位保证不会在同一个压缩窗口内反复压缩。

### 2.3 Token 估算：不用 tokenizer 的轻量方案

一个关键问题：如何估算当前消息列表的 token 消耗？加载完整 tokenizer 太重，但需要做出触发决策。

ARF 使用字符级启发式估算：

```python
def _estimate_tokens(messages):
    total = 0
    for m in messages:
        content = m.get("content", "") or ""
        reasoning = m.get("reasoning_content", "") or ""
        total += len(content) + len(reasoning)
        for tc in m.get("tool_calls", []) or []:
            total += len(json.dumps(tc.get("function", {})))
    return int(total * 0.4)  # 混合中英文 ~0.4 token/char
```

这不是精确计数——但对于"是否超过 75% 阈值"的判断来说，误差在可接受范围内。不需要加载 tokenizer，不增加内存开销，计算完全在内存中进行（几毫秒）。

### 2.4 上下文窗口可配置

DeepSeek V4 是 1M，但如果未来有其他模型呢？ARF 把 `context_window` 作为模型配置字段：

```yaml
# models/deep_thinking/config.yaml
name: deep_thinking
model_type: deep_thinking
context_window: 1048576   # 1M
config:
  base_url: "..."
  api_key: "..."
  model_name: "deepseek-v4-pro"
  max_tokens: 100000      # 输出限制
  ...
```

每个模型可以声明自己的上下文窗口大小。压缩阈值（75%）基于这个值计算。

---

## 三、渐进式工具结果披露：别把所有内容都塞进上下文

Agent 最费 token 的地方往往不是对话，而是工具返回结果。

典型场景：一个 `file_reader` 读取了 5000 行的代码文件，返回 10 万字，全部塞进下一次 LLM 调用的上下文里。而 LLM 可能只需要其中 3 行。

ARF 的做法是：**工具结果超过 2000 字符时，自动截断并存盘**。

### 3.1 流程

```
工具返回 15000 字符 → 前置 500 字符 + "完整内容已存盘" → 消息上下文
                      ↓
               完整内容写入 workspace/tool_results/xxx.txt
```

LLM 看到的消息内容变成：

```
[工具返回的前 500 字符内容...]

... [输出截断: 完整结果 15000 字符, 已存储: 20260601_120000_file_reader_call_abc.txt]
请用 file_reader 工具读取该文件以获取完整内容: /workspace/tool_results/20260601_120000_file_reader_call_abc.txt
```

### 3.2 "需要时再加载"

LLM 知道完整结果的存储路径。当它确实需要完整内容时，可以调用 `file_reader`（ARF 的内核工具）去读取。这形成了一种**懒加载**（lazy loading）模式：

- 大部分情况：LLM 从 500 字符摘要就能判断结果是否相关
- 少数情况：LLM 主动"翻硬盘"获取完整内容

就像 IDE 里的文件预览——默认只显示摘要，双击才打开全文。

### 3.3 阈值可配置

2000 字符不是硬编码的魔法数字，而是工作区配置项：

```yaml
# arf_agent.yaml
agent:
  max_tool_result_chars: 2000  # 可根据场景调大或调小
```

---

## 四、整体效果：1M 窗口的"寿命"延长了多少？

以一个典型的 2 小时编程 Agent 会话为例（模拟数据）：

| 阶段 | 无压缩 token 消耗 | 有压缩 token 消耗 | 节省 |
|------|------------------|------------------|------|
| 前 5 轮（< 3 轮窗口） | ~30K | ~30K | 0%（未触发） |
| 5-15 轮（超过 3 轮，触发压缩） | ~300K | ~80K | 73% |
| 15-30 轮（长期运行） | ~800K | ~120K | 85% |
| 工具结果（30 次调用，平均 5K 字符/次） | ~60K | ~15K | 75% |

| | 无优化 | ARF 优化后 | 最大会话轮数（1M 窗口内） |
|---|---|---|---|
| 早期对话 | 线性增长 | 压缩为固定摘要 | 从 ~20 轮延长到 50+ 轮 |
| 工具输出 | 无限制 | 2000 字符截断 + 存盘 | 75% 节省 |
| 系统提示词 | ~800 token | ~800 token | 无变化 |

通过压缩 + 渐进式披露的组合，1M 上下文窗口的"有效寿命"大约延长了 **3-5 倍**。

---

## 五、实现细节

### 5.1 压缩节点在 LangGraph 中的位置

```python
# 图结构（简化）
workflow.add_node("compact", compact_node)
workflow.add_edge("compact", "call_model")

# 所有指向 call_model 的边都先经过 compact
workflow.add_conditional_edges("execute_tools", route_after_tools, {
    "call_model": "compact",  # 工具执行后 → compact → call_model
    "respond": "respond",
})
```

压缩节点不阻塞主流程——如果不需要压缩，它只是一个空操作（return {}）。

### 5.2 did_attempt_compact 标志位

压缩只做一次。节点检查 `state["has_attempted_compact"]` 标志位——如果为 True，直接跳过。这防止了在压缩窗口附近反复压缩的抖动问题。

### 5.3 工具结果存储位置

```
workspace/
├── tool_results/              # 渐进式披露的完整文件
│   ├── 20260601_120000_file_reader_call_abc.txt
│   └── 20260601_120500_grep_results_call_def.txt
├── memory/
├── models/
└── ...
```

文件命名格式：`{时间戳}_{工具名}_{call_id}.txt`。便于按时间排序和按工具名检索。

---

## 六、与其他方案的对比

| 方案 | 实现复杂度 | 压缩质量 | 信息丢失 | 上下文控制 |
|------|----------|----------|---------|-----------|
| **无压缩（原始）** | 无 | N/A | 无 | 窗口爆了就截断 |
| **纯滑动窗口（截断式）** | 低 | 低 | 大量丢失 | 固定窗口 |
| **全量摘要（mem0 风格）** | 中 | 中 | 细节可能丢失 | 可控制 |
| **ARF 滑动窗口 + 摘要** | 中 | 高 | 关键信息保留 | 自动 + 可配置 |
| **向量化检索（RAG 风格）** | 高 | 高 | 上下文碎片化 | 需要额外服务 |

ARF 的方案不需要额外的向量数据库或检索服务。它是纯文本的、文件系统原生的——这和 ARF "工作区即代码"的哲学一致。

---

## 七、局限与未来

当前方案的已知局限：

1. **压缩模型是固定的 `quick_no_thinking`** — 未来可以让用户指定压缩模型
2. **摘要 3000 字符上限** — 对极长的早期对话，摘要可能不够详细
3. **压缩只执行一次** — 真正的长时间会话可能需要多次压缩（当前有标志位保护）
4. **工具结果是文件存储** — 没有自动清理机制，老旧文件可能堆积

后续迭代会考虑：
- 分层压缩：摘要也可以再被压缩（递归摘要）
- 智能阈值：根据任务类型动态调整压缩阈值
- 工具结果自动过期清理

---

## 开源地址

- **Gitee**: [https://gitee.com/dalaydata/open_deepseek_arf](https://gitee.com/dalaydata/open_deepseek_arf)
- **GitHub**: [https://github.com/Wang-hubber/open_deepseek_arf](https://github.com/Wang-hubber/open_deepseek_arf)

完整的上下文压缩实现在 `src/arf/engine/nodes.py` 的 `compact_node` 函数和 `src/arf/engine/graph.py` 的图构建代码中，欢迎阅读源码和贡献。

如果你觉得有用，欢迎 Star ⭐
