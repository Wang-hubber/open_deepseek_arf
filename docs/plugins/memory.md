# Memory Plugin — Long-term Memory Extraction

ARF Memory 插件提供跨会话长期记忆的自动提取与管理能力。记忆输出为单文件 `memory/memory.md`（≤300KB），会话启动时自动加载并注入 system prompt。

**核心设计**：抽取在 subprocess 中异步执行，不阻塞引擎主循环。系统模型（sysmodel）根据精心设计的 prompt 模板从会话中筛选跨会话有效的事实，跳过任务状态、调试细节和一次性对话。

---

## 1. 架构

```
Agent 会话
    │
    ├─ 每 round 结束 → [Hook] round_end
    │       │
    │       └─ round_end.py: 检查 round % interval == 0 ?
    │              ├─ No  → 跳过
    │              └─ Yes → Popen extractor.py
    │                          │
    │                          ├─ 读取 FileStateStore（消息历史）
    │                          ├─ 读取现有 memory.md
    │                          ├─ 读取 prompt.md（模板）
    │                          ├─ 调用 sysmodel（deepseek-v4-flash）
    │                          ├─ 原子写入 memory.md（.tmp → os.replace）
    │                          └─ 退出
    │
    └─ 用户对话内 → memory_extract 工具调用
           └─ function.py → Popen extractor.py（同上流程）
```

## 2. 目录结构

```
arf/plugins/memory/
├── tools/
│   └── memory_extract/
│       ├── tool.yaml       # 工具定义（kernel 激活）
│       ├── function.py     # 分发器：读 FileStateStore → Popen
│       ├── extractor.py    # Subprocess 入口
│       └── prompt.md       # 抽取 prompt 模板（可替换）
└── hooks/
    └── round_end.py        # 轮次间隔触发器
```

## 3. 配置

在 `agent.yaml` 中声明插件：

```yaml
plugins:
  - name: memory
    config:
      interval: 10          # 每 N 个 rounds 触发一次抽取 (default: 10)
      memory_dir: ./memory  # 记忆文件目录 (default: ./memory)

advanced:
  memory:
    resident_file: memory.md  # 常驻记忆文件名 (default: memory.md)
    max_size_kb: 300          # 单文件最大大小 (default: 300)
```

## 4. 触发方式

### 4.1 轮次间隔触发（默认）

`round_end` hook 在每轮用户交互结束时触发，检查 `round % interval == 0`。满足条件时启动 subprocess 执行抽取。不阻塞会话。

### 4.2 用户手动触发

在对话中调用 `memory_extract` 工具——"提取当前会话中的长期记忆"、"记住这个项目用 Rust"。

### 4.3 用户直接编辑

`memory/memory.md` 是标准 Markdown 文件，可直接编辑。框架启动时自动加载。

## 5. 抽取逻辑

### 5.1 Prompt 设计原则

`prompt.md` 模板指导 sysmodel 区分跨会话事实与一次性对话细节：

- **提取**：用户身份、偏好、架构决策（含 WHY）、持久性事实
- **跳过**：任务进度、工具结果、调试过程、一次性对话
- **输出**：结构化 Markdown（`## Category` + `- ` bullets），无空分类

### 5.2 写入安全

```
extractor.py
    ├─ 读取现有 memory.md
    ├─ 调用 sysmodel，获得新内容
    ├─ 写入 memory.md.tmp（新内容）
    ├─ copy2(memory.md, memory.md.bak)（旧内容备份）
    ├─ os.replace(memory.md.tmp, memory.md)（原子替换）
    └─ os.remove(memory.md.bak)（清理）
```

写入中途崩溃：原文件完好。内容超 300KB：自动截断尾部整行，追加截断警告注释。

## 6. 自定义

### 6.1 自定义 prompt 模板

替换 `arf/plugins/memory/tools/memory_extract/prompt.md` 即可。模板中 `{{EXISTING_MEMORY}}` 会被替换为当前 `memory.md` 内容。

### 6.2 自定义提取器

替换 `extractor.py`，保持 CLI 接口一致即可：

```
python extractor.py --session-file <json> --memory-dir <dir> --session-id <id>
```

### 6.3 禁用插件

移除 `agent.yaml` 中 `plugins:` 字段的 `memory` 条目即可。框架无记忆功能正常运作。

## 7. 加载

会话启动时，`BaseAgent.__init__()` 调用 `_load_resident_memory()` 读取 `memory/memory.md`，注入 system prompt 的 `{{MEMORY}}` 占位符。文件不存在时返回空字符串，不报错。
