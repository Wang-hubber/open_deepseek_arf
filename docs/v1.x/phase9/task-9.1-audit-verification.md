# 任务 9.1：抽象病灶探查验证

> Phase 9 第一项任务
> 父文档：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`（commit `ef4fe43`）

## 设计思路

本任务是**纯探查**，不写任何代码。目标把父 spec §4.2 六条预判病灶的真实状态探明：每条找 file:line 锚点 + 描述现状 + 重判 + 影响面。

输出文件 `docs/v1.x/phase9/audit-verification.md`（独立文件，独立 commit）。

约束（与父 spec §5 一致）：
- **不修代码**
- **不出 fix 决策 / fix-spec**
- **不动 docs/api/**

逐行探查要点：每个病灶的每条探查命令后都跟一行解释，看代码的人能在 HEAD 上重放。

---

## 工作步骤（6 条病灶）

### 病灶 A3-001 探查 — ToolSpec 双类型

**Step 1：定位两个 `pub struct ToolSpec`**

```bash
grep -rn 'pub struct ToolSpec' crates/arf-core/src/ crates/arf-agent/src/
```

逐行解释：
- `-r` 递归；只搜 arf-core 与 arf-agent 两个 crate 的源码目录
- 期望输出两行：core 一处、agent 一处；记录各自 file:line

**Step 2：字段对比**

```bash
grep -A 5 'pub struct ToolSpec' crates/arf-core/src/tool.rs
grep -A 8 'pub struct ToolSpec' crates/arf-agent/src/tool.rs
```

逐行解释：
- `-A N` 显示后续 N 行，能看到 struct 完整字段
- 把两段输出并排比，列重叠字段 vs 仅存字段

**Step 3：Engine 实际 import 哪份**

```bash
grep -n 'ToolSpec\|use.*tool::' crates/arf-engine/src/engine.rs crates/arf-engine/src/registry.rs
```

逐行解释：
- 在 engine 入口与注册表代码里找 ToolSpec 的具体引用
- 该 file:line 直接证 framework 装配路径用哪一份

**判定**：
- 若两 struct 字段重叠 ≥ 2 个 **且** Engine import 与声明层不匹配 → 维持 §4.2 预判 A3-001（F）
- 若 Engine import 明确统一到一份 → 重判：仍 F 但影响面收窄

---

### 病灶 A3-002 探查 — `use_skill` 协议归属

**Step 1：定位 `use_skill` 协议**

```bash
grep -rn 'use_skill\|UseSkill\|SkillLoad' crates/arf-core/src/ crates/arf-mcp/src/
```

逐行解释：
- 期望 arf-core/message.rs 有 `UseSkill` 类型（来自 message.rs 8 个内置 ActionMessage 一员）；记录 file:line
- 期望 arf-mcp/node.rs 的 `message_loop` 有 `use_skill` 处理分支

**Step 2：是否实现 ActionMessage**

```bash
grep -n 'impl ActionMessage for.*UseSkill\|impl.*ActionMessage.*UseSkill' crates/arf-core/src/message.rs
```

逐行解释：
- 期望命中一行 `impl ActionMessage for UseSkill {...}`
- 若未命中 → use_skill 不是 Bus 协议，违反 Phase 6 设计原则

**Step 3：协议形态**

```bash
grep -A 4 'pub.*UseSkill' crates/arf-core/src/message.rs
```

逐行解释：
- 看 UseSkill 的字段（用什么作为 payload）
- 同其他 ActionMessage 比对（intent / msg_type）

**判定**：
- 若 `impl ActionMessage for UseSkill` 存在 → 维持 A3-002
- 若 use_skill 走 McpNode 内部事件 → 重判：违反 Phase 6 边界，**触 A1-S2（doc 多职责或超范畴）**，应升级报告

---

### 病灶 A3-003 探查 — 静态与 skill tool 表合并

**Step 1：两表定位**

```bash
grep -n 'pub.*tool_map\|fn tool_map\|pub.*SkillIndex' crates/arf-mcp/src/discovery.rs crates/arf-mcp/src/skill.rs
```

逐行解释：
- `DiscoveryBackend::tool_map()`（discovery.rs）
- `SkillIndex`（skill.rs）
- 记录两表各自 file:line

**Step 2：交叉引用**

```bash
grep -rn 'tool_map\|SkillIndex' crates/arf-mcp/src/
```

逐行解释：
- 看 Engine 路由 tool 时如何合并 / 引用这两个表
- 命中点决定合并时机

**Step 3：McpNode 持有关系**

```bash
grep -n 'discovery:\|skill:\|SkillIndex' crates/arf-mcp/src/node.rs
```

逐行解释：
- McpNode 同时持有 DiscoveryBackend 与 SkillIndex？记录两个字段
- 若 McpNode 不持 SkillIndex → skill 由谁管理？

**判定**：
- 若两表同时存在且无显式合并点 → 维持 A3-003（F / A3-S4 + A4-S4）
- 若运行时某方法做合并 → 维持但补充合并点 file:line

---

### 病灶 A3-004 探查 — AgentConfig V1 / V2 并存

**Step 1：定位 V1 / V2**

```bash
grep -n 'pub struct AgentConfig' crates/arf-agent/src/config.rs crates/arf-engine/src/config.rs
```

逐行解释：
- 期望两行：V1 在 agent（line 24），V2 在 engine（line 7）
- 各自 file:line 记入报告

**Step 2：所有引用点**

```bash
grep -rn 'AgentConfig' crates/ py-arf/src/ tests/ examples/
```

逐行解释：
- 列每个 import 点
- 区分 `use arf_agent::AgentConfig`（V1）vs `use arf_engine::AgentConfig`（V2）

**Step 3：EngineBuilder 消费**

```bash
grep -n 'config: AgentConfig\|fn build' crates/arf-engine/src/builder.rs
```

逐行解释：
- 看 `EngineBuilder::build` 接收什么类型
- 直接证 V1 是否进入 Engine 装配入口

**Step 4：py-arf 入口**

```bash
grep -rn 'AgentConfig\|agent_config' py-arf/src/
```

逐行解释：
- py-arf 是用户入口；它暴露 V1 还是 V2 给用户
- 记 file:line

**判定**：
- 若 V1 仅有 declaration 但未被 import（除自身 crate 外）→ 维持 A3-004 预判
- 若 V1 有 py-arf / examples 实际引用 → 重判：影响面缩小到声明层

---

### 病灶 A3-005 探查 — `State` 双 crate

**Step 1：是否存在 arf_state 引用**

```bash
grep -rn 'use arf_state\|arf_state::' crates/ py-arf/src/ tests/
```

逐行解释：
- 期望零命中
- 若有命中，逐个记录

**Step 2：arf-state 全文结构**

```bash
wc -l crates/arf-state/src/lib.rs
grep -n 'pub struct\|pub enum\|^impl' crates/arf-state/src/lib.rs
```

逐行解释：
- 行数 + 公开 API 表面
- 列出 arf-state::State / Task 与 arf-core::State 重叠 / 差异

**Step 3：Task 双向锁设计意图**

```bash
grep -B 2 -A 6 'pub struct Task' crates/arf-state/src/lib.rs
```

逐行解释：
- 看 doc comment 是否说明 Task 双向锁设计意图（blocked_by + blocking 双向引用）
- 对比 arf-core::state 模块是否同样实现

**判定**：
- 若 `use arf_state` 零命中 + 双向锁未在 arf-core 实现 → 维持 A3-005（dead feature）
- 若双向锁已在 arf-core 实现但仍保留 arf-state → 维持并附"可删"备注（但 spec 不出 fix）

---

### 病灶 A3-006 探查 — ResourceSpec.resource_name alias

**Step 1：定位字段**

```bash
grep -n -B 2 'pub resource_name\|#\[serde' crates/arf-agent/src/resource.rs
```

逐行解释：
- 看 resource_name 字段定义上下文，确认 `#[serde(alias = "name")]` 是否真的存在
- 记录 file:line

**Step 2：消费方引用**

```bash
grep -rn '"name"\|resource_name' py-arf/src/ examples/ tests/
```

逐行解释：
- 看用户 / 例子 / 测试用 `"name"` 还是 `"resource_name"`
- 命中统计 → alias 是否真的服务调用

**判定**：
- 若 alias 存在但用户已切到 resource_name → 维持（潜在 P3 兼容尾巴）
- 若 alias 仍是主调用形态 → 维持并扩大影响面

---

## 关键设计决策

- **探查过程 read-only**：不动任何代码（包括不动 Cargo.toml / 不改 .gitignore）
- **每条病灶的判定都有 file:line 锚点**：用 `Read` 或 `grep -n` 在 HEAD 上可复现
- **不写 fix 决策**：报告仅事实与重判，影响面，给后续 phase 留 fix 入口
- **不入 docs/api/**：报告进 `docs/v1.x/phase9/audit-verification.md`，spec 边界（§5.3）

---

## 验证命令（self-review）

报告写完后，逐条用以下命令复现（每个 file:line 锚点对应一条）：

```bash
grep -n 'pub struct ToolSpec' crates/arf-core/src/tool.rs crates/arf-agent/src/tool.rs
grep -n 'pub struct AgentConfig' crates/arf-agent/src/config.rs crates/arf-engine/src/config.rs
grep -rn 'use arf_state' crates/
grep -rn 'use_skill\|UseSkill' crates/arf-core/src/message.rs
grep -n 'tool_map\|SkillIndex' crates/arf-mcp/src/discovery.rs crates/arf-mcp/src/skill.rs
grep -B 2 'pub resource_name' crates/arf-agent/src/resource.rs
```

每条命令都应能复现报告中的 file:line。

---

## 下一步

1. 用户批准本 task doc → 开始探查（6 条病灶按 Step 1 → 2 → ... 顺序走）
2. 探查结果整理为 `docs/v1.x/phase9/audit-verification.md`，按 §3.3 schema 逐条登记
3. self-review（占位 / 不一致 / scope）
4. commit + 请用户审已 commit 版本

后续 phase（stage 9.2+）不在本任务范围，留待本任务产出后再决策。
