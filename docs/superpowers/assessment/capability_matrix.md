# ARF Framework Capability Matrix

> Assessment Date: 2026-05-18
> Based on: resource_inventory.csv + source code analysis

## 维度 1: 资源 CRUD（自进化核心路径）

### 1.1 Tool CRUD

| 操作 | 系统能力 | 状态 | 说明 |
|------|---------|------|------|
| Scaffold | resource_scaffold skill | ✅ | Full prompt_template for tool.yaml + function.py generation with error logging boilerplate |
| Validate | validate_tool skill | ✅ | Checks YAML structure, required fields (name/description/schema), function.py existence, execute() callable, plus dry-run with minimal test inputs |
| Write | file_writer | ✅ | File content creation/overwrite at any path; tools/ directory restricted to Sys Agent via USER_RESTRICTED_PREFIXES |
| Register | file_writer + hot-reload | ⚠️ | No dedicated "register" action. file_writer writes files to workspace, then manager.reload_user() detects new directories as "+tools/name". Two-step process, not single-command. |
| Update | file_reader + file_writer | ✅ | file_reader reads existing tool.yaml/function.py, file_writer overwrites with changes |
| Delete | file_deleter + hot-reload | ✅ | file_deleter soft-deletes (renames with _deleted suffix); hot-reload detects removed directories as "-tools/name" |
| Dependency check | check_deps() via resource_registrar | ✅ | resource_registrar action="check_deps" calls Registry.check_deps() which walks depends_on list and reports unconfigured dependencies |
| Discovery | resource_loader | ⚠️ | resource_loader.list_active shows discoverable (registered-but-inactive) tools, but does not scan workspace -- relies on pre-loaded registry state |

### 1.2 Skill CRUD

| 操作 | 系统能力 | 状态 | 说明 |
|------|---------|------|------|
| Scaffold | resource_scaffold skill | ✅ | Prompt_template includes "If generating a SKILL" section with skill.yaml template (name, description, prompt_template, tools, parameters) |
| Validate | validate_tool skill | ❌ | validate_tool only validates tool.yaml + function.py + execute(). Skills have skill.yaml (no function.py, no execute). No validate_skill skill exists. |
| Write | file_writer | ✅ | Writes skill.yaml content; skills/ path restricted to Sys Agent via USER_RESTRICTED_PREFIXES |
| Register | file_writer + resource_loader | ⚠️ | No dedicated register. file_writer writes skill.yaml, hot-reload picks up new skill directory, resource_loader can reference it. Three-step chain, no single command. |
| Update | file_reader + file_writer | ✅ | Read existing skill.yaml, overwrite with modifications |
| Delete | file_deleter | ✅ | Soft-delete via _deleted suffix on skill.yaml; hot-reload detects removed skill directory |

### 1.3 Model CRUD

| 操作 | 系统能力 | 状态 | 说明 |
|------|---------|------|------|
| Scaffold | resource_scaffold skill | ❌ | Prompt_template only covers TOOL and SKILL sections. No MODEL generation template exists. |
| Validate | (none specific) | ❌ | No validate_model skill or tool exists in the framework. Model config correctness is not validated. |
| Write | model_manager.create / file_writer | ✅ | model_manager action="create" writes config.yaml from structured params; file_writer also works for models/ path (Sys Agent only) |
| Register | model_manager tool | ✅ | model_manager.create writes config.yaml to models/<name>/ directory, which is detected by hot-reload as a new model resource |
| Update | model_manager.update / file_writer | ✅ | model_manager action="update" merges provided fields (model_type, base_url, api_key, etc.) into existing config.yaml |
| Delete | model_manager.delete / file_deleter | ✅ | model_manager action="delete" renames config.yaml to config.yaml_deleted; file_deleter uses _deleted suffix. Both soft-delete. |

### 1.4 Hook CRUD

| 操作 | 系统能力 | 状态 | 说明 |
|------|---------|------|------|
| Create | manage_hooks → add_hook() | ✅ | action="add" validates event/name/command, checks duplicate names, writes entry to .hooks.json |
| Read | manage_hooks → list_hooks() | ✅ | action="list" returns all hooks grouped by 6 events with full metadata (command, timeout, enabled, matcher) |
| Update | manage_hooks → update_hook() | ✅ | action="update" merges provided fields (command, timeout, enabled, matcher) onto existing hook by event + name |
| Delete | manage_hooks → remove_hook() | ✅ | action="remove" deletes hook entry from event list in .hooks.json |
| Trigger | HookRunner.run() on 6 events | ⚠️ | HookRunner supports all 6 events (SessionStart, PreModelCall, PostModelCall, PreToolUse, PostToolUse, SessionEnd) with parallel subprocess execution and exit-code contract. However, manage_hooks tool.yaml enum only allows 4 events (missing PreModelCall, PostModelCall), limiting agent-accessible event types. |

## 维度 2: Agent 运行时自治

| 能力 | 实现路径 | 状态 | 说明 |
|------|---------|------|------|
| 模型自动路由 | Classifier → quick_thinking / deep_thinking | ✅ | classifier.py 调用 LLM-based classify_request() 将请求分类为 medium/ complex, 映射为 quick_thinking / deep_thinking 模型类型; UserAgent YAML 中 classifier_enabled: true, SysAgent 中 false; 含退化链 (deep_thinking 不可用时回退 quick_thinking) |
| 显式模型切换 | router._requests_model_change() | ✅ | router.py 中 _requests_model_change() 检测中英文切换词组 ("切换到"/"switch to"/"use deep" 等) 和复杂度跃迁关键词 ("架构"/"refactor"/"system"), 路由到 classify_node 重新分类; 此外 model_switch 工具和 model_manager switch action 均可通过 _resolve_model_switch() 直接变更 current_model 状态 |
| 短期记忆管理 | session.md + memory_extract | ⚠️ | _memory_section() 将 memory/session.md 加载到系统提示词中 (2000 字截断); session_archiver 钩子在 SessionEnd 保存全量会话到 sessions/*.json (保留最近 10 条); memory_extractor 钩子提取到 extracted_memories.md。但缺少专门的 session.md 管理工具, 需 agent 用 file_writer 读写 |
| 长期记忆管理 | long_term.md + memory_store | ✅ | memory_store 工具支持 read/write/stats/compress 四种操作, 写入 memory/long_term.md, 1MB 硬限制, 70% 阈值触发 compression_needed 标记, 写入前自动备份轮换; _long_term_memory_section() 将其加载到提示词 (4000 字截断); memory_extract 和 memory_management 技能提供操作指引 |
| 记忆压缩 | memory_compress skill | ✅ | memory_compress skill.yaml 提供完整压缩流程; memory_store action compress 可通过 model 参数调用指定模型自动压缩, 也可由 agent 手动压缩后写入; 备份轮换机制 (long_term_{ts}_bak.md) 保留最新备份 |
| 渐进式工具加载 | Kernel tools + resource_loader | ✅ | resource_loader 支持 activate/deactivate/list_active; kernel 工具 (file_reader/file_writer/file_deleter/resource_loader) 不可被 deactivate; activate 前自动检查依赖 (check_deps); 系统提示词中的 discoverable 列表指导用户发现并使用休眠工具 |
| 上下文窗口管理 | sliding-window + summary compaction | ✅ | compact_node 在每次 call_model 前执行; 当 tokens 超过 context_window 的 75% 时, 保留最近 3 轮对话, 将旧轮次用 quick_no_thinking 模型压缩为结构化摘要 (中文提示词); 含 has_attempted_compact 标记避免重复压缩; token 估算采用启发式 (~0.4 tokens/char), 非真实 tokenizer |
| 错误恢复 | error_handler skill → recovery node | ✅ | recovery_node 处理 max_tokens 续写 (最多 3 次) 和 API 错误两种场景; graph 中 recovery -> compact -> call_model 构成恢复循环; 3 次连续工具失败时注入 model_switch 建议; error_handler skill.yaml 提供自愈协议 (错误分类 -> 最多 2 次重试 -> 升级报告) |
| Hook 编排 | HookRunner 6 事件 | ⚠️ | HookRunner 支持全部 6 个事件 (SessionStart/PreModelCall/PostModelCall/PreToolUse/PostToolUse/SessionEnd), 默认配置 6 个钩子 (system_log ×5 + session_archiver + memory_extractor); 但 manage_hooks 工具仅暴露 4 个事件到 YAML 枚举 (缺少 PreModelCall/PostModelCall), agent 无法运行时管理全部钩子类型 |
| 热加载 | ResourceRegistry.reload_user() | ✅ | reload_user() 重新扫描工作区目录, 返回变更描述列表 (如 +tools/foo, ~skills/bar, -tools/baz); base.py 中 _reload_registry_if_needed() 在 file_writer/file_deleter 后自动触发; SessionManager.get_agent() 通过 mtime 对比自动重建 agent |
| 依赖检查 | check_deps() | ✅ | Registry.check_deps() 遍历资源 depends_on 列表, 返回未配置依赖项; resource_loader._handle_activate() 在激活前调用; resource_registrar 工具提供配置依赖的管理入口 |
| 双 Agent 协作 | Dispatcher → UserAgent handoff → SysAgent | ✅ | Dispatcher 实现两阶段流水线: UserAgent (classifier_enabled, 受限工具集) 先执行, 检测到 handoff_to_sys 调用后进入 SysAgent (深度思考, 完整工具集); handoff 消息携带意图/动作/原因结构化字段; 支持 streaming 和非 streaming 两种模式; 两阶段用量合并返回 |

## 维度 3: 用户任务类别覆盖

### A — 文件操作

| 子能力 | 对应工具/技能 | 状态 | 说明 |
|--------|-------------|------|------|
| 读取文件 | file_reader | ⚠️ | 仅支持纯文本 UTF-8 读取 (`read_text`)，不支持 PDF/二进制/图片格式；支持目录列表 (list 操作)；config_default 标注为非必需 (required: false) |
| 写入文件 | file_writer | ✅ | 完整文本写入 (`write_text`)，自动创建父目录；User Agent 受限制无法写入 tools/skills/models 路径（需 handoff_to_sys）；config_default 非必需 |
| 删除文件 | file_deleter | ⚠️ | 实现为软删除（重命名为 `_deleted` 后缀），非真删除；User Agent 路径限制同 file_writer；config_default 非必需 |
| 文件下载 | file_download | ⚠️ | 仅生成 `/api/download?file=` 类型 URL（需外部 HTTP 服务器承载），非直接文件传输；包含工作区路径逃逸检查；config_default 非必需 |
| 批量文件处理 | 编排 file_reader + file_writer | ❌ | 无批量/glob/通配符处理能力，file_reader 和 file_writer 均为单文件操作；需 agent 自行循环编排实现批处理 |

### B — 信息获取

| 子能力 | 对应工具/技能 | 状态 | 说明 |
|--------|-------------|------|------|
| 网页抓取 | web_fetch | ⚠️ | HTTP GET 获取原始 HTML/文本内容，带浏览器类 User-Agent；200KB 截断限制；**无 HTML→Markdown 转换**（返回原始内容）；无限流控制；config_default 非必需 |
| 网页搜索 | web_search | ❌ | CONFIG_STUB — 仅有 `config_default.yaml` 声明，无 `function.py`；框架完全不支持网页搜索 |
| RAG 检索 | rag_operator | ❌ | CONFIG_STUB — 仅有 `config_default.yaml` 声明，无 `skill.yaml`；虽声明依赖 embedding + rerank 模型，但无实际检索实现 |
| 数据库查询 | db_operator | ✅ | 完整 skill.yaml，带 5 种 SQLite 操作 (create_table/insert/update/query/list_tables)；参数化查询防注入；数据文件存储在 workspace/data/ 下；无其他数据库支持 (仅 SQLite)；config_default 无外部依赖 |

### C — 资源创建（自进化）

| 子能力 | 对应工具/技能 | 状态 | 说明 |
|--------|-------------|------|------|
| 生成新 Tool | tool_generator → resource_scaffold | ✅ | 完整 skill.yaml，引导 agent 完成需求分析 → 参数设计 → resource_scaffold 生成 tool.yaml + function.py；resource_scaffold 技能提供详细模板含错误日志样板代码 |
| 生成新 Skill | skill_generator → resource_scaffold | ✅ | 完整 skill.yaml，类似 tool_generator 但输出 skill.yaml 模板；生成后调用 validate_tool 验证结构 |
| 生成新 Model 配置 | (不存在) | ❌ | model_generator 技能完全不存在（目录不存在）；模型配置通过 model_configurator 技能手动添加（非生成式），需用户交互提供 base_url/api_key 等字段 |
| 校验生成的资源 | validate_tool | ⚠️ | 仅校验 Tool 资源 (tool.yaml + function.py + execute())；Skill 资源无对应 validate_skill，skill_generator 中虽引用 validate_tool 但其仅能检查 YAML 结构 |
| 激活/注册资源 | resource_registrar + resource_loader | ⚠️ | resource_loader 支持 activate/deactivate/list_active（含依赖检查）；但无统一"注册"命令——写入→检测→激活为三步链；resource_registrar 仅管理依赖配置，不执行注册 |
| 克隆系统资源 | (不存在) | ❌ | 框架内无 clone/copy 功能；用户无法将系统资源模板复制到工作区进行自定义；需通过 file_reader 读取系统资源内容再 file_writer 手动复制 |

### D — 数据分析

| 子能力 | 对应工具/技能 | 状态 | 说明 |
|--------|-------------|------|------|
| SQL 查询 | db_operator | ✅ | 完整 skill.yaml 支持 query 操作；仅 SQLite 数据库；支持参数化查询；数据文件存于 workspace/data/ |
| 数据格式转换 | 编排 file_reader + Tool | ⚠️ | 无专用格式转换工具；可通过 file_reader 读取源格式 + agent 代码逻辑处理转换 + file_writer 输出；依赖 agent 编程能力，非框架内置 |
| 报表/图表生成 | 需用户 Tool/Skill 支持 | ❌ | 框架内无图表/报表生成工具或技能；无可视化支持；无 chart/graph/report 关键词出现于任何系统技能定义中 |
| 日志分析 | 编排 web_fetch / file_reader + deep_thinking | ⚠️ | 无专用日志分析工具；可通过 file_reader 读取日志 + deep_thinking 模型分析；依赖 agent 推理能力，非框架内置分析管道 |

### G — 对话增强

| 子能力 | 对应工具/技能 | 状态 | 说明 |
|--------|-------------|------|------|
| 短期记忆 | session.md + memory_extract | ⚠️ | `_memory_section()` 自动加载 memory/session.md 到系统提示词 (2000 字截断)；`memory_extract` 技能指导从对话中提取信息写入长期记忆；但无专用 session.md 管理工具，agent 需用 file_writer 手动写入 |
| 长期记忆 | long_term.md + memory_store | ✅ | memory_store 工具提供 read/write/stats/compress 四操作；1MB 硬限制，70% 触发 compression_needed；写入前自动备份轮换；`_long_term_memory_section()` 加载到提示词 (4000 字截断) |
| 记忆压缩 | memory_compress | ✅ | 完整 skill.yaml 指导压缩流程 (找 flash model → 自动或手动压缩 → 验证)；memory_store action="compress" 可通过模型自动压缩；备份保留最新 `long_term_{ts}_bak.md` |
| 上下文保留 | sliding-window compaction | ✅ | `compact_node` 在 call_model 前执行：token 超 context_window 75% 时保留最近 3 轮，旧轮次用 quick_no_thinking 模型压缩为结构化摘要；`has_attempted_compact` 防重复压缩 |
| 模型配置 | model_configurator + model_manager | ✅ | 完整 skill.yaml 支持 add/modify/test 三操作；从系统 slot 的 config_default.yaml 读取模板；通过 model_manager 工具有效管理模型配置；需用户交互提供敏感字段 |

## 维度 4: 跨切面关注点

| 关注点 | 检查项 | 状态 | 说明 |
|--------|--------|------|------|
| 热加载 | 文件变化检测 → reload_user() | ✅ | `__init__.py` 使用 `watchfiles.awatch()` 异步监控 tools/、skills/、models/ 目录；检测到变化后调用 `ResourceRegistry.reload_user()` 重新扫描工作区，返回结构化的变更描述列表（如 `+tools/foo`、`~skills/bar`、`-tools/baz`）；含 1 秒防抖避免频繁触发 |
| 热加载 | 新工具同 session 内可用 | ⚠️ | Registry 内存状态实时刷新，但 `SessionManager.get_agent()` 缓存的 Dispatcher 仅在模型配置 mtime 变化时重建。通过 file_writer 新建的工具写入 Registry 但缓存的 Agent 工具列表未更新，LLM 无法立即感知新工具。需模型配置变更或服务器重启才能生效。 |
| 可观测性 | SQLite trace 存储 | ✅ | 6 张表完整体系：`sessions`（会话元数据）、`usage_records`（用量）、`model_pricing`（定价）、`trace_events`（完整追踪事件含 node/duration/tokens/status/error/metadata）、`message_feedback`（点赞/点踩）、`session_cost`（会话成本）；双写机制同时写入 SQLite 和 workspace memory/traces/\*.jsonl；通过 `insert_trace_events()` 在生产路径写入 |
| 可观测性 | TraceView 瀑布图 | ✅ | 613 行完整 Vue 3 组件（`frontend/src/views/TraceView.vue`），包含 ECharts 瀑布图集成、会话列表侧栏、按 Turn 分组时间线、节点分类（classify/call_model/execute_tools/hook/respond/recovery）、工具调用详情展开、Hook 执行状态、Token 消耗显示、反馈记录；状态颜色编码（OK 绿/Error 红/Blocked 橙/Skipped 灰） |
| 流式输出 | SSE streaming + WebSocket | ✅ | SSE 流式端点 `/api/chat?stream=true` 通过 `_stream_chat()` 推送 chunk/tool_call/tool_result/done/error 事件；WebSocket 端点 `/ws` 支持 token 参数认证；`_stream_chat()` 包含流式 trace 双写、用量记录、标题自动生成、SessionEnd 钩子触发 |
| 流式输出 | handoff 事件发射 + 前端处理 | ⚠️ | 后端 `Dispatcher.run_stream()` 在 UserAgent → SysAgent 切换时发射 `{"type": "handoff", ...}` 事件。但前端 `useChat.ts` 的 `handleEvent()` 未处理 `evt.type === 'handoff'`，不存在对应分支，handoff 事件被静默忽略。前端全域 grep "handoff" 零结果。用户界面无任何 handoff 过渡标记。 |
| 前端 | 组件数量与视图 | ✅ | 13 个组件 + 7 个视图。组件：ChatPanel、CollapsibleSection、DeepSeekConfigForm、FeedbackPopup、MessageBubble、ModalDialog、OpenAIConfigForm、RegistrationDialog、ResourcePanel、SessionPanel、StatusBar、ToolCard、UsageBar。视图：ChatLayout、ConfigPage、ResourceDetailView、ResourceStatsView、TraceView、UsagePage、WelcomePage。国际化支持：zh-CN.json、en-US.json。 |
| 前端 | 资源管理 UI（增删改查） | ✅ | 三层资源管理界面：ResourcePanel 组件（工作区资源列表与状态指示）、ResourceDetailView 视图（单个资源详情与配置）、ResourceStatsView 视图（调用统计、成功率、平均耗时、CSV 导出）。配合 ConfigPage 视图实现模型注册与配置。 |
| 测试 | 单元/集成测试覆盖 | ⚠️ | 39 个测试用例分布在 2 个文件中（test_dual_agent.py: 23 个、test_audit_fixes.py: 16 个）。覆盖领域：agent 配置框架/工作区合并、handoff_to_sys 工具鉴定/原因/提示词、file_writer/file_deleter 路径限制（user/sys 双模式）、Dispatcher handoff 检测/提取/提示构建、系统工具注入审计、trace 双写与 hook 事件验证、懒加载工具函数、critical_rules 配置、DEEPSEEK_MODEL_SPECS 移除验证。 |
| 测试 | 边界场景覆盖 | ⚠️ | 含负面测试（handoff 负例、缺失文件、路径逃逸阻止），但大面积模块无任何测试：classifier.py、router.py、graph.py 图节点（compact/recovery）、resource_scaffold skill、tool_generator/skill_generator skills、model_manager tool、manage_hooks tool、memory_store tool、web_fetch tool、session_manager.py、tracing.py、server/routes.py。无 pytest 运行环境可用。 |
| 安全 | UserAgent 路径限制 | ✅ | file_writer 和 file_deleter 的 `USER_RESTRICTED_PREFIXES = ("/tools/", "/skills/", "/models/")` —— User Agent 模式下检查包含关系和前缀匹配，阻止写入/删除受保护路径，提示调用 handoff_to_sys 转交。 |
| 安全 | 用户资源不覆盖系统资源 | ⚠️ | 部分实施。`_register()` 方法：models 类型将用户配置合并到系统默认之上（config merge，保留系统字段）；tools/skills 类型用户版本覆盖系统版本（注释说明"clone workflow"设计意图），但保留系统 metadata（depends_on、required、config_template、config_page）。 |
| 安全 | Hook 管理认证保护 | ❌ | Hook 管理 API 端点（`GET /api/hooks`、`POST /api/hooks`、`PUT /api/hooks/{name}`、`DELETE /api/hooks/{name}`）无任何认证保护：无 JWT 校验、无 Bearer token、无 Depends() 认证依赖、无 session/cookie 检查。routes.py 全局无 auth 中间件或装饰器（单用户模式默认假设可信网络环境）。 |
