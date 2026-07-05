# multi_agent_team 示例 app

> ARF 新设计（V1.x Phase 7）的端到端示例：**多 agent 团队 + 子代理池 + SSE 事件流聚合 + 人工审批**。

本示例把 Task 1-12 的所有框架能力串成一个可运行的 FastAPI 应用：
1 个 `pm` 项目经理 agent + 3 个 data 领域 agent + 2 个 subagent pool（tool_creator / prompt_tuner）+ 3 个工具（list_dir / read_file / write_file）+ 1 个人工审批通道。

---

## 5 分钟上手

```bash
# 1. 安装示例 app（开发模式）
cd examples/multi_agent_team
pip install -e ".[dev]"

# 2. 设置 DeepSeek API Key
export DEEPSEEK_API_KEY=sk-...   # 或在 agents/*/agent.yaml 里直接改

# 3. 启动服务（默认监听 127.0.0.1:8000）
python server.py

# 4. 打开另一个终端，订阅团队 SSE 事件流
curl -N http://127.0.0.1:8000/sse/team/default

# 5. 在第三个终端，发一条 user message 给 pm
curl -X POST http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "请帮我梳理一下数据接入的流程"}'

# 6. 委派任务到 tool_creator pool
curl -X POST http://127.0.0.1:8000/delegate/tool_creator_pool \
  -H 'Content-Type: application/json' \
  -d '{"message": "写一个把 CSV 转成 JSON 的工具"}'

# 7. 查 / 解决待审批请求（ask-mode 工具触发时才会有）
curl http://127.0.0.1:8000/approvals
curl -X POST http://127.0.0.1:8000/approve/<request_id> \
  -H 'Content-Type: application/json' \
  -d '{"approved": true}'
```

如果只是验证框架接线、不跑真实模型，可以跳过第 2 步直接启动——`/chat` 会返回 `{"status": "skeleton", ...}` 而不是真实回复（因为 Task 8 的 `team.engine(id)` 仍是 skeleton）。

---

## 目录结构

```
examples/multi_agent_team/
├── pyproject.toml            # 示例 app 元数据 + pytest 配置（e2e marker）
├── conftest.py               # 注册 --run-e2e 选项
├── README.md                 # 本文档
├── server.py                 # FastAPI 入口（lifespan 启动 Team + SseRelay）
├── approval.py               # 内存版人工审批注册表（框架无关）
│
├── teams/                    # Team YAML 声明
│   └── default.yaml          # 4 persistent engines + 2 subagent pools
│
├── agents/                   # 每个 agent 一份目录
│   ├── pm/                   # 项目经理（agent.yaml + system_prompt.md）
│   ├── data_onboarding/      # 数据接入
│   ├── data_governancer/     # 数据治理
│   ├── data_explorer/        # 数据探索
│   ├── tool_creator/         # 子代理：动态生成工具
│   └── prompt_tuner/         # 子代理：调优 prompt
│
├── tools/                    # 工具注册表（每个工具 = 目录）
│   ├── list_dir/             # 列出目录内容
│   ├── read_file/            # 读文件
│   └── write_file/           # 写文件（ask-mode，需要人工审批）
│
├── data/
│   └── events/               # Engine JSONL 持久化目录（运行时生成）
│
├── shared_workspaces/        # 跨 agent 共享工作目录（tool I/O 落地）
│
└── tests/
    └── test_basic_flow.py    # 冒烟测试：TeamConfig + SseFormatter + 服务启动
```

---

## 架构概览

```
                                ┌─────────────────────────────┐
HTTP POST /chat ───────────────▶│        pm (持久 engine)      │
HTTP POST /delegate/<pool> ───▶│  + 3 data engines (持久)    │
                                │  + 2 subagent pools (临时)   │
                                └─────────────┬───────────────┘
                                              │
                          Bus (peer_message, subagent_delegate)
                                              │
                                ┌─────────────▼───────────────┐
SSE GET /sse/team/<team_id> ◀───│  SseRelay → EventFilter     │
                                │  (聚合所有 engine 的 JSONL) │
                                └─────────────────────────────┘
```

### 关键设计

- **Engine 单写 JSONL**：每个持久 engine 把每条事件 append 到 `data/events/<engine>.jsonl`，崩溃后可从 `Last-Event-ID` 续传。
- **SubagentPool 自动回收**：临时子代理处理完任务后归还到池里，超过 idle timeout 自动清理。
- **Team YAML 声明**：`teams/default.yaml` 是单一事实源，`TeamConfig.from_yaml()` 解析后由 `TeamBuilder.build()` 启动。
- **peer_message 跨 agent 通讯**：所有持久 engine 都 `auto_subscribe: peer_message`，pm 可以广播到任何 peer。
- **SSE 实时事件流聚合**：`SseRelay` 监听所有成员的 JSONL 文件，按 `Last-Event-ID` 过滤后转 SSE 推给浏览器。
- **崩溃 outbox resend**：未确认的事件先入 outbox，重启后自动补发。
- **人工审批**：ask-mode 工具（这里是 `write_file`）调用前先在 `ApprovalRegistry` 注册 pending 请求，HTTP 调用 `/approve/<id>` 后才放行。

---

## 验证清单（Verification Checklist）

每完成一步跑一次对应命令，全部绿即可视为本机环境就绪：

| # | 验证项 | 命令 | 预期 |
|---|--------|------|------|
| 1 | py-arf 安装 | `pip install -e .` (in repo root) | `Successfully installed arf-...` |
| 2 | 示例 app 安装 | `pip install -e ".[dev]"` (in example dir) | 无报错（见下方 caveat） |
| 3 | 冒烟测试 | `pytest tests/ -q` | `2 passed, 1 skipped` |
| 4 | 服务启动 | `python server.py` | `team booted (team_id=default, ...)` |
| 5 | 健康检查 | `curl http://127.0.0.1:8000/health` | `{"status": "ok", "team_started": true}` |
| 6 | chat 路由 | `curl -X POST .../chat -d '{"message":"hi"}'` | 200 + `{"status":"skeleton"\|"response":...}` |
| 7 | 委派路由 | `curl -X POST .../delegate/tool_creator_pool -d '...'` | 200 + skeleton/result |
| 8 | SSE 订阅 | `curl -N .../sse/team/default` | `id: ...\nevent: ...\ndata: {...}\n\n` 流 |
| 9 | 审批查询 | `curl .../approvals` | `{"pending": []}` 或带 ID |
| 10 | E2E（可选）| `pytest tests/ -q --run-e2e` | 1 passed 或 framework skeleton 提醒 |

> **Caveat — pip install ".[dev]"**：示例 app 的 pyproject 用 setuptools 自动发现包，但仓库根下的 `data/teams/agents/shared_workspaces` 顶层目录会被误识别为包。如果遇到 `Multiple top-level packages discovered` 报错，可以在 pyproject 加 `[tool.setuptools] packages = ["multi_agent_team"]` 显式声明，或在 venv 里 `pip install -e ".[dev]" --no-build-isolation` 绕过。

---

## 展示的 ARF 新设计能力

- Engine 单写 JSONL 持久化（V1.x Task 1-2）
- Engine 崩溃恢复 + checkpoint（Task 4）
- Ephemeral Engine（一次性 engine，用完即弃；Task 3）
- SubagentPool + OutboxStrategy + PoolMetrics（Task 5）
- `py_arf.relay`：JsonlTailer + SseFormatter（Task 6）
- TeamMembership + EventFilter + SseRelay（Task 7）
- TeamConfig YAML + TeamBuilder.build()（Task 8）
- peer_message 跨 agent 通讯（Task 10 wiring）
- FastAPI lifespan + Bus 注入（Task 12）

---

## 已知 TODO（不在本 Task 范围）

- `team.engine(id) -> EngineHandle` 仍是骨架——`/chat` 返回 `status: skeleton` 而不是真实回复。
- `SseRelay.stream()` 暂时只吐一次文件标记，不做真正的 per-event 异步循环。
- `data/events/` 当前为空——Engine 还没接上 `JsonlTailer`，需要后续增量。
- `peer_topology` 字段被 `TeamConfig` 忽略（forward-compat hint）。

完整 TODO 列表见 [`.superpowers/sdd/task-13-brief.md`](../../.superpowers/sdd/task-13-brief.md) 的 "Known gaps" 节。