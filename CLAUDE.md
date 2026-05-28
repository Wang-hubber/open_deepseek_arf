# ARF — AI Resources & Runtime Framework

> **Harness = OS Kernel. Model = CPU. Agent = Computer.**
> Token 是指令，Agent 会话是进程，工具调用是系统调用。

ARF 提供 Agent 运行所需的全部基础设施——资源发现与热加载、内存管理、模型调度、安全沙箱、可观测性、A2A 通讯。框架通过 `agent.yaml` 声明式装配，App 层只需关注"用什么"，不需要知道"怎么实现"。

---

## 架构边界

| 层 | 目录 | 职责 |
|----|------|------|
| **框架** | `arf/` | 执行引擎、资源系统、Agent 组装、基础设施、17 个 Protocol 抽象 |
| **参考 App** | `app/arf_default_assistant/` | 通过配置消费框架全部能力，验证框架设计的完备性 |
| **设计文档** | `docs/*.md` | 每篇含 OS 方案演进 → 当前实现 → 演进方向 三章 |
| **App 指南** | `APP开发者指南.md` | 面向 App 开发者，从 0 到 1 构建 ARF 应用 |

**核心原则**：框架提供 mechanism（怎么做），App 通过 configuration + instantiation 决定做什么。`agent.yaml` 是桥接点。

---

## 关键目录

```
arf/
├── agent/          # BaseAgent — DI 组装全部 Protocol 实现
├── engine/         # GraphEngine — invoke/astream 主循环 + HandoffManager
├── core/           # 17 个 Protocol 定义 + Pydantic 配置模型 + ModelAdapter
├── resources/      # 三个 Provider + ResourceResolver + FileWatcher
├── memory/         # FileMemoryStore、LLMMemoryWriter、LLMMemoryRetriever
├── compaction/     # SlidingWindowCompactor — token 感知窗口压缩
├── routing/        # TwoTierRouter — 快慢模型调度
├── guardrails/     # PathCheckToolGuard、ToolPermissionChecker
├── hooks/          # SubprocessHookRunner — 六个生命周期事件
├── sandbox/        # PathSandbox — 路径合法性校验
├── communication/  # InMemoryAgentBus、PeerAgent、Supervisor、Lock、Consensus
├── human_loop/     # ApprovalPoint、ConsoleChannel — 人机审批
├── observability/  # FileTraceStore、UsageTracker
├── streaming/      # SSE 事件流
├── evaluation/     # EvalRunner、BenchmarkBuilder、EvalComparator
├── testing/        # 14 个 InMemory* test doubles
├── errors/         # DefaultErrorPolicy + FunctionBackend rollback
├── concurrency/    # SequentialScheduler
└── skills/         # SkillPipeline — 工具依赖执行时序

app/arf_default_assistant/
├── agent.yaml      # 唯一配置文件（框架从此装配全部能力）
├── server.py       # FastAPI + SSE streaming + SPA fallback
├── cli.py          # init / start / stop / chat / list / validate / config
├── models/         # deep.yaml + quick.yaml（文件系统是真相源）
├── tools/          # 每个工具一个子目录：tool.yaml + function.py
├── skills/         # 技能定义 YAML
├── hooks/          # Hook 脚本
└── memory/         # 运行时数据（框架自动生成，不提交）
```

---

## 开发约定

### 提交信息

所有 commit message 必须以 `Co-Authored-By: Claude Code with DeepSeek V4` 结尾。

提交风格：`type(scope): description`（如 `feat(engine):`, `refactor(agent):`, `docs:`, `test:`, `style:`, `fix:`）。

### 语言

- 与用户沟通使用**中文**
- 代码、技术术语、commit message 使用 **English**
- 文档：框架设计文档中英均可，README 中英双语

### 代码风格

- Python 3.11+，所有新文件必须有模块 docstring
- 类型标注：公开 API 用 `dict[str, Any]`（非裸 `dict`）
- Protocol 优先于 ABC 继承——依赖反转，接口隔离
- 工具函数签名必须为 `async def execute(...) -> dict`
- 公开接口变更需更新对应 Protocol

### 测试

- 测试目录：`tests/`
- 运行：`pytest tests/ -q` 或 `.venv/bin/pytest tests/ -q`
- Test doubles 在 `arf/testing/`，每个有 `reset()` 方法和调用记录
- 新增功能需写测试（TDD 优先：先写失败测试 → 实现 → 验证通过）

---

## 常用操作

```bash
# 安装
pip install -e ".[dev]" -i https://pypi.mirrors.ustc.edu.cn/simple

# 启动参考 App
cd app/arf_default_assistant
python cli.py start                    # 构建前端 + 启动 server (端口 8000)

# 运行测试
pytest tests/ -q                       # 全部测试
pytest tests/ -q -m "not slow"         # 跳过慢测试

# 校验 agent.yaml
python cli.py validate
```

---

## 文档体系

| 文档 | 受众 | 内容 |
|------|------|------|
| `README.md` / `README.zh-CN.md` | 所有人 | 问题域架构表 + 框架/应用分层 + 演进方向 |
| `APP开发者指南.md` | App 开发者 | 从 0 到 1 构建 ARF 应用（12 章） |
| `docs/memory-management.md` | 框架开发者 | 内存管理：OS 演进 → 当前实现 → 演进 |
| `docs/model-routing.md` | 框架开发者 | 模型调度：缓存层次类比 → TwoTierRouter |
| `docs/resource-registry.md` | 框架开发者 | 资源注册：systemd/udev 类比 → FileWatcher |
| `docs/tool-sandbox.md` | 框架开发者 | 安全边界：保护环类比 → 三道防线 |
| `docs/skill-pipeline.md` | 框架开发者 | 并发：超标量类比 → SkillPipeline |
| `docs/a2a-communication.md` | 框架开发者 | A2A 通讯：IPC 类比 → HandoffManager/AgentBus |
| `docs/interrupt.md` | 框架开发者 | 中断回滚：硬件中断类比 → Cancel/Undo |
| `docs/trace.md` | 框架开发者 | 可观测性：事件系统 → FileTraceStore |
| `docs/eval-benchmark.md` | 框架开发者 | 回归测评：会话回放 → EvalRunner |
| `docs/app/dual-agent.md` | App 开发者 | 双 Agent 架构：配置、handoff、权限分离 |
| `docs/app/tools.md` | App 开发者 | 工具编写完整参考 |
| `docs/app/skills.md` | App 开发者 | 技能与流水线 |
| `docs/app/models.md` | App 开发者 | 模型配置 |
| `docs/app/hooks.md` | App 开发者 | 生命周期 Hook |
| `docs/app/advanced.md` | App 开发者 | Memory/Routing/Compaction/Guardrails 全部字段 |

---

## 重要注意事项

- **不要提交 App 运行时数据**：`app/arf_default_assistant/memory/`、`workspaces/`、`logs/` 是框架自动生成的，不应进入版本控制
- **不要绕过沙箱**：文件操作必须经过 PathCheckToolGuard（`..` 穿越和绝对路径被框架拦截，工具层不需要重复校验）
- **不要内联模型配置到 agent.yaml**：模型定义放在 `models/*.yaml`，`agent.yaml` 仅覆盖需要微调的字段
- **资源约定优于配置**：工具/技能/模型按约定目录放置，FileWatcher 自动检测，无需手动注册
- **框架重构不破坏 App**：修改 `arf/` 内部实现时，保持 Protocol 和 `agent.yaml` schema 向后兼容
- **依赖注入优先**：不要硬编码具体实现，通过 `BaseAgent.__init__(**override_protocols)` 注入替代
- **仅提交框架文档和 App 开发者文档**：`docs/*.md`（框架设计文档）和 `APP开发者指南.md` 可以提交。`docs/superpowers/` 下的计划/规格/审计文档、以及其他临时性文档（如 `docs/SELF_REVIEW.md`）不应进入版本控制——它们是开发过程的中间产物，不是交付物
