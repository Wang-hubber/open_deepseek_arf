# Memory — 三层长期记忆

ARF Memory 提供跨会话长期记忆，分三层独立管理：**project**（项目结构）、**user**（用户事实）、**secrets**（加密凭证）。MemoryIndex 统一读写与注入，MemoryPlugin 在 round_end 自动提取用户记忆。

**核心设计**：三层各自独立开关、独立存储、独立注入为 system message。project.md 在首次会话自动生成，user.md 由 LLM 定期提取，secrets.enc 由工具调用读写。

---

## 1. 三层架构

```
data/memory/
├── project.md      # 项目结构、架构、约定 — ProjectMemoryGenerator 生成
├── user.md         # 用户身份、偏好、决策 — MemoryPlugin._rolling_update() 更新
└── secrets.enc     # 加密凭证 — SecretsStore (XOR, ARF_MASTER_KEY)
```

| 层 | 文件 | 写入者 | 触发时机 | 注入形式 |
|----|------|--------|---------|---------|
| project | `project.md` | `ProjectMemoryGenerator` | 首次会话，fire-and-forget | `## Project Memory` system msg |
| user | `user.md` | `MemoryPlugin._rolling_update()` | 每 N 轮 round_end | `## User Memory` system msg |
| secrets | `secrets.enc` | `SecretsStore` (工具调用) | 用户通过 `write_secret` 工具 | `## Available Secrets` system msg |

---

## 2. MemoryIndex — 中央管理器

`MemoryIndex` 是三层记忆的唯一读写入口，BaseAgent 在 init 时构造并注入 ControlPlane。

```python
from arf.memory.config import MemoryConfig
from arf.memory.index import MemoryIndex

mem_cfg = MemoryConfig(
    project={"enabled": True, "auto_generate": True},
    user={"enabled": True, "extract_interval": 5},
    secrets={"enabled": True, "master_key_env": "ARF_MASTER_KEY"},
)
idx = MemoryIndex(data_dir="./data", config=mem_cfg, secrets_store=secrets_store)
```

### 注入流程

`ControlPlane._build_system_messages()` 在每次会话开始时调用 `MemoryIndex.build_injected_messages()`，为每个启用的层生成独立 system message：

```
System: ## Project Memory          ← project.md 内容
System: ## User Memory             ← user.md 内容
System: ## Available Secrets       ← 已存储的 secret 名称列表
```

### 校验

- secrets 启用但 `SecretsStore` 为 None（未配 `ARF_MASTER_KEY`）→ 直接抛 `RuntimeError`，不静默降级
- project/user 文件不存在时返回空字符串，不报错

---

## 3. Project Memory — 项目结构

### 生成时机

BaseAgent init 时，若 `project.md` 不存在且 `auto_generate: true`，`ProjectMemoryGenerator` 自动触发：

```
BaseAgent.init
  → ProjectMemoryGenerator.needs_generation()  → 文件不存在?
    → _scan() 扫描目录树 + README + pyproject.toml
    → _build_prompt() 拼接生成 prompt
    → call_model(prompt) → LLM 生成 project.md
```

生成是 fire-and-forget（`asyncio.create_task`），不阻塞启动。

### 输出格式

```markdown
# Project Overview
... (one paragraph)

# Architecture
... (key directories and their roles)

# Key Conventions
- ...

# Dependencies
- ...
```

---

## 4. User Memory — 用户事实提取

### 触发机制

`MemoryPlugin` 挂载 `round_end` hook，每 N 轮触发一次（默认 5 轮）：

```
round_end → MemoryPlugin.on_hook()
  → round % interval == 0 ?
    → asyncio.create_task(_rolling_update())
      → 加载现有 user.md
      → 取最近 20 条消息构建 prompt
      → call_model(prompt) → LLM 提取用户事实
      → save_user(content) → user.md
```

### 提取规则

LLM prompt 指导只提取**用户维度**事实：

- **提取**：用户身份（角色/技能/背景）、偏好（语言/风格/工具/沟通方式）、决策（含 WHY）、跨会话知识（领域约束/联系人）
- **跳过**：任务进度、代码/工具输出、调试堆栈、一次性对话、项目结构（那是 project.md 的职责）

### 输出格式

```markdown
## User Identity
- Backend engineer, primary language Go

## Preferences
- Prefers concise code with no comments
- 使用中文沟通, code and technical terms in English

## Decisions
- PostgreSQL over MongoDB — ACID transaction requirement

## Knowledge
- Pipeline bugs tracked in Linear project "INGEST"
```

无新事实时 LLM 输出 `NO_NEW_MEMORY`，不覆盖现有文件。

---

## 5. Secrets Store — 加密凭证

### 加密方案

XOR 加密存储在 `secrets.enc`，密钥来自环境变量 `ARF_MASTER_KEY`。

```python
from arf.memory.secrets_store import SecretsStore

store = SecretsStore(data_dir="./data", master_key="my-secret-key")
store.set("GITHUB_TOKEN", "ghp_xxx")
token = store.get("GITHUB_TOKEN")  # "ghp_xxx"
```

### 工具接口

三个工具可供 Agent 调用：

| 工具 | 功能 |
|------|------|
| `write_secret` | 写入/更新一个 secret |
| `read_secret` | 读取一个 secret 的值 |
| `list_secrets` | 列出所有已存储的 secret 名称 |

注入时只暴露名称列表，不暴露值。Agent 需要通过 `read_secret` 工具按需读取。

---

## 6. 配置

```yaml
# agent.yaml
plugins_config:
  memory:
    project:
      enabled: true           # 启用 project.md
      auto_generate: true     # 首次自动生成
      max_size_kb: 100        # 单文件上限
    user:
      enabled: true           # 启用 user.md
      extract_interval: 5     # 每 N 轮触发提取
      max_size_kb: 50         # 单文件上限
    secrets:
      enabled: true           # 启用 secrets.enc
      master_key_env: ARF_MASTER_KEY  # 密钥环境变量名
```

所有字段都有默认值，不配也能跑——但 secrets 需要设 `ARF_MASTER_KEY`，否则 MemoryIndex 构造时直接报错。

### 禁用某一层

```yaml
plugins_config:
  memory:
    secrets:
      enabled: false   # 不需要 secrets
```

---

## 7. 数据流全景

```
BaseAgent.init
├── SecretsStore(data_dir, ARF_MASTER_KEY)     ← 读 env var
├── MemoryIndex(data_dir, config, secrets_store)
│     └── secrets.enabled ∧ store is None → RuntimeError
├── ProjectMemoryGenerator(root, memory_index)
│     └── needs_generation()? → fire-and-forget LLM → save_project()
└── ControlPlane.set_memory_index(memory_index)

会话运行中:
  round_end → MemoryPlugin.on_hook()
    → _rolling_update() → LLM → memory_index.save_user()

会话开始 (ControlPlane._build_system_messages):
  → memory_index.build_injected_messages()
    → project.md → "## Project Memory"
    → user.md    → "## User Memory"
    → secrets    → "## Available Secrets"
```
