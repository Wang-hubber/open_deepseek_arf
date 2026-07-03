# audit-probe-9.6.1：skill_list_progressive（L1 元数据 list）端到端探查

> Task 9.6.1 探查产出 — **Framework 是否能让 app 只列 skill 名+描述、不读 body？**
> 父 task doc：`docs/v1.x/phase9/task-9.6.1.md`
> 父 spec：`docs/v1.x/phase9/capability-matrix-and-audit-design.md`
> 前置：9.5.1（FsDiscovery 端到端 OK）
> **本 task 探查：FsDiscovery::scan 扫 SKILL.md frontmatter + list_skills() L1 list + advertised shape + 缺字段 skip**

---

## §A 探查环境

- working tree：HEAD `107c56b`（task 9.5.1）+ uncommitted `crates/arf-e2e/tests/skill_list_progressive.rs`
- 测试文件：`crates/arf-e2e/tests/skill_list_progressive.rs`（4 test cases）
- 驱动：4 mock（tmpdir + SKILL.md 含 frontmatter）
- 测试命令：
  ```bash
  cargo test -p arf-e2e --test skill_list_progressive -- --nocapture --test-threads=1
  ```
- 结果：**`4 passed; 0 failed`**（tmpdir 写 SKILL.md + scan + list_skills）
- 关键运行输出：
  ```
  test skill_list_advertised_shape ...
  [test3] advertised shape: 2 skills
  [test3]   - greet: Greet user (body=false, content=false, text=false)
  [test3]   - code_review: Review code changes (body=false, content=false, text=false)
  [test3] advertised skills 形状（L1 metadata, 无 body）端到端 OK ✓
  test skill_list_does_not_load_body ...
  [test2] SkillEntry type: arf_mcp::skill::SkillEntry
  [test2] skill_a body len = 4848 bytes (确认 body 需显式加载)
  [test2] L1 list 不加载 body + L2 body 显式按需加载 端到端 OK ✓
  test skill_list_missing_frontmatter_skipped ...
  [test4] list_skills() 返回 1 skills（坏 SKILL.md 应被 skip）
  [test4]   - good
  [test4] 缺 frontmatter / 缺 name / 缺 description 全部 skip 端到端 OK ✓
  test skill_list_returns_metadata_only ...
  [test1] list_skills() = 2 skills
  [test1]   - greet: Greet user politely (compat=Some("v1.0"))
  [test1]   - summarize: Summarize long text (compat=None)

  test result: ok. 4 passed; 0 failed; 0 ignored
  ```

### 凭据安全（self-check 已通过）

```bash
$ git grep -n 'sk-' -- crates/arf-e2e/tests/skill_list_progressive.rs   # 无输出
```

---

## §B (capability, 情景) 单元判定

### 单元 1：`list_skills()` L1 元数据完整性

```
单元              : skill_list_progressive × §2.3
能力等级           : D（PASS）
判定依据          : tmpdir 写 2 SKILL.md，FsDiscovery::scan → list_skills() 返回 2 个 SkillEntry
                   name / description / compatibility 3 字段均回填
file:line         : crates/arf-mcp/src/skill.rs:68-122 (SkillIndex::scan)
                   crates/arf-mcp/src/skill.rs:128-130 (list_index → list_skills)
                   crates/arf-mcp/src/discovery.rs:153-155 (FsDiscovery impl)
                   ✓ SkillEntry.name / description / compatibility / source_dir 4 字段
                   ✓ 缺 frontmatter / 缺 name / 缺 description 全部 skip + warn
```

### 单元 2：L1 list 不加载 body（progressive 实证）

```
单元              : skill_load_on_demand × §2.3（progressive boundary）
能力等级           : D（PASS）
判定依据          : 5KB body × 3 SKILL.md，list_skills() 阶段不调 load_skill_body
                   SkillEntry Debug 输出确认无 body 字符串字段
                   load_skill_body("skill_a") 显式调用返 4848 bytes
file:line         : crates/arf-mcp/src/skill.rs:132-136 (load_body: 独立方法)
                   crates/arf-mcp/src/skill.rs:128-130 (list_index: 不调 load_body)
                   ✓ L1 list 与 L2 body 加载明确分层
```

### 单元 3：advertised skills 形状

```
单元              : skill_list_progressive × §2.3（advertised boundary）
能力等级           : D（PASS）
判定依据          : 重放 McpNode::build_node_info (node.rs:93-95) 的 advertised format
                   skill entry 仅含 {name, description}，**不**含 body / content / text
file:line         : crates/arf-mcp/src/node.rs:93-95
                   let skills: Vec<Value> = self.discovery.list_skills().iter()
                       .map(|s| serde_json::json!({"name": s.name, "description": s.description}))
                       .collect();
                   ✓ advertised L1 metadata 端到端无 body
```

### 单元 4：缺字段 skip 行为

```
单元              : skill_list_progressive × §2.3（error boundary）
能力等级           : D（PASS）
判定依据          : 4 SKILL.md（1 合法 + 3 坏）→ list_skills() 返 1
                   坏 SKILL.md 形态：
                   - 缺 frontmatter（无 `---`）
                   - 有 frontmatter 但缺 name
                   - 有 frontmatter 但缺 description
                   全部触发 eprintln! WARNING + skip
file:line         : crates/arf-mcp/src/skill.rs:90-99 (parse_frontmatter None → warn + continue)
                   crates/arf-mcp/src/skill.rs:101-107 (is_kebab_case fail → warn, 但**仍** insert)
                   ✓ 坏 SKILL.md 端到端 skip + warn
```

---

## §C 探查产出汇总

| 单元 | 等级 | 判定依据 |
|---|---|---|
| `skill_list L1 metadata × §2.3` | **D** | 3 字段完整 + 坏 SKILL.md skip |
| `progressive L1 vs L2 × §2.3` | **D** | list 不调 body + body 显式按需加载 |
| `advertised shape × §2.3` | **D** | advertised 仅 {name, description}，**无** body |
| `error boundary × §2.3` | **D** | 缺 frontmatter / name / description 全部 skip + warn |

---

## §D 病灶登记

**本 task 无新增 F-lesion**（framework skill_list_progressive 端到端 OK）。

### 框架实际行为（按 spec §3.3 输出）

- `FsDiscovery::scan` 扫 `root/skills/*/SKILL.md` —— **D 端到端**（L1 list + L2 body 分离）
- `SkillIndex::list_index()` 返 `Vec<&SkillEntry>` —— **D**（仅元数据，4 字段）
- `DiscoveryBackend::list_skills()` 委托 SkillIndex —— **D**
- `load_skill_body()` 独立按需加载 —— **D**（L1 vs L2 明确分层）
- `McpNode::build_node_info` advertised skills 仅 `{name, description}` —— **D**（**无** body 字段）
- 缺 frontmatter / 缺 name / 缺 description 全部 skip + warn —— **D**（不 panic，不污染 list）

### 注意事项（潜在 issue，非 lesion）

1. **`is_kebab_case` 只 warn 不 reject**（skill.rs:101-107）—— `code_review` 这种 snake_case 命名仍被注册进 list，**仅** eprintln warning。**不算** lesion（设计选择，渐进迁移），但**潜在 issue**：app 端发 `use_skill("code_review")` 时会 match（不依赖 kebab-case 强制）。建议 doc 化此行为。
2. **`compatibility` 字段仅入 SkillEntry，无 runtime check**—— 9.6.1 探查范围内 framework **不** 校验 compatibility 是否匹配当前 runtime 版本。**不算** lesion（schema 设计选择），但 spec §2.5-插 隐含的"按需载入"含 compatibility 过滤，需独立 task 探查。
3. **`is_kebab_case` warn 是 stderr eprintln!**（skill.rs:103）—— app 端若想"严格拒绝非法命名"无 hook，**只能** in-memory 二次过滤。**不算** lesion（设计选择），但 spec 应明确 progressive layer 的"strict mode"。

### 探查信号命中（§4 find signals）

跑 spec §4.2 find signals：

- **A1-S1**（trait 方法多职责）：`DiscoveryBackend` trait 有 7 方法（tool 3 + skill 4 + resource 1 + run_skill_tool 1），但每个方法**单一职责**（list/resolve/load/run），**无** `and / or / with_xxx` 模式。**未命中**。
- **A3-S1**（同名字段跨 crate）：`SkillEntry` 仅在 `arf-mcp::skill` 一处定义，**无** 跨 crate 同名。**未命中**。
- **A4-S1**（filter 散落）：`list_skills()` 直接返 `Vec<&SkillEntry>`，无 filter 动词散落。**未命中**。
- **A4-S2**（validate 散落）：`parse_frontmatter` 在 `SkillIndex::scan` 单一接缝，**无** 散落。**未命中**。

---

## §E 探查回归

- 9.5.1 既有 4 test pass（FsDiscovery + McpNode + DiscoveryBackend + ScriptTool）
- 9.6.1 新增 4 test pass
- 综合：9.5-9.6 = 8 test，**全 pass**，0 新 F-lesion
- 与既有 lesion（F-001~F-009）**无关**——本 task 探查 skill_list L1 边界，不触达 model / pool / stream / thinking

---

## §F 与父 task / spec 的关系

| 父 task 期望 | 实证结果 |
|---|---|
| list_skills() 返 L1 metadata 完整 | ✓ test1 pass（name + description + compatibility） |
| L1 list 不加载 body | ✓ test2 pass（SkillEntry Debug 无 body 字段 + load_body 显式按需） |
| advertised shape 仅 L1 | ✓ test3 pass（{name, description}，**无** body） |
| 缺字段 skip 行为 | ✓ test4 pass（4 个坏 SKILL.md → 1 个 good） |
| 预期 0 新 F-lesion | ✓ 0 新 F-lesion |

> 结论：9.6.1 探查显示 framework **skill_list_progressive** 端到端 work——app 通过 `FsDiscovery::scan` 即可拿到 L1 metadata 列表，body 显式按需加载（L2），advertised 到 bus 时**不**带 body。这是 phase 9 首次在 skill L1 layer 探查无 F-lesion 的 task。

---

## §G 提交状态

- 工作目录：`crates/arf-e2e/tests/skill_list_progressive.rs`（~235 行，4 test cases）
- task doc：`docs/v1.x/phase9/task-9.6.1.md`（新增）
- audit probe：本 doc
- lesion-registry：**未变**（0 新 F-lesion）
- 待 commit
