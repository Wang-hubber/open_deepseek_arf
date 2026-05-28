# ARF Fact-Check Report — 2026-05-28 — Resource Registry

## Summary
- **Total tests**: 84
- **Passed**: 84
- **Automated findings**: 7 (0 Critical, 4 Warning, 3 Info)
- **Deep manual findings**: 1

## Findings

### Warning — DOC BUG: Provider 构造参数名不一致 (§2.2, line 133)

**Doc 声称**: "所有 Provider 接受 `fs_root` 参数，默认 `./`"

**代码实际**: ToolProvider 接受 `tools_dir`，SkillProvider 接受 `skills_dir`，ModelProvider 接受 `models_dir`。不存在 `fs_root` 参数。

**测试**: `test_constructor_accepts_tools_dir_not_fs_root`, `test_constructor_accepts_skills_dir_not_fs_root`, `test_constructor_accepts_models_dir_not_fs_root`

**建议**: 将文档改为 "所有 Provider 接受各自目录参数（`tools_dir`/`skills_dir`/`models_dir`）" 或考虑未来统一为 `fs_root` + 子目录约定。

### Warning — DOC BUG: reload endpoint 路径错误 (§2.3, line 156)

**Doc 声称**: `POST /api/resources/reload` 对标 `systemctl daemon-reload`

**代码实际**: 端点是 `POST /api/reload`（`app/arf_default_assistant/routers/resources.py:117`）

**测试**: `test_reload_endpoint_path`

**建议**: 将文档中的 `/api/resources/reload` 改为 `/api/reload`。

### Warning — DOC BUG: DefaultToolResolver 被描述为"别名"但实际是包装类 (§2.4, line 182)

**Doc 声称**: "`DefaultToolResolver = ResourceResolver` 别名保留旧接口"

**代码实际**: `DefaultToolResolver` 是一个独立的包装类，构造函数接受 `providers`/`retriever`/`backend` 参数，内部委托给 `ResourceResolver`。不是别名（不是 `DefaultToolResolver = ResourceResolver`）。

**测试**: `test_default_tool_resolver_is_separate_class`

**建议**: 将文档改为 "`DefaultToolResolver` 是向后兼容的包装类，内部委托给 `ResourceResolver`"

### Warning — DOC BUG: Doc 引用 `_load_all()` 但代码使用 `_load()` (§2.1 line 108, §2.5 line 198)

**Doc 声称**: Provider 使用 `_load_all()` 方法进行惰性加载

**代码实际**: 所有三个 Provider 的私有加载方法都是 `_load()`，非 `_load_all()`。`StaticYamlToolProvider` 有 `_load_all()` 方法，但该类未被任何其他代码使用。

**测试**: `test_provider_method_is_private_load_not_load_all`

**建议**: 将文档中的 `_load_all()` 引用改为 `_load()`。

### Info — 完整性: PluginProvider 未在文档中提及

`PluginProvider`（`arf/resources/providers/plugin_provider.py`）已实现并集成到 `BaseAgent`（`arf/agent/base.py:154-158`），支持扫描 `arf/plugins/{name}/` 下的 tools/ 和 skills/。文档 §3.2 将 MCP 多源 Provider 列为"演进方向"，但基本的 PluginProvider 已经实现。

**测试**: `test_plugin_provider_exists_in_code`

### Info — 完整性: StaticYamlToolProvider 未在文档中提及且未被使用

`StaticYamlToolProvider`（`arf/resources/providers/static_yaml.py`）是另一个 ToolProvider 实现，但它没有被框架中的任何其他代码导入或使用。属于死代码或实验性代码。

**测试**: `test_static_yaml_tool_provider_exists_in_code`

### Info — DOC BUG: _FrozenDict 文档字符串与文档展示不一致 (§2.3, line 141)

**Doc 展示**: `"""对标 systemd 的静态单元缓存——init 时加载，之后不可变。"""`

**代码实际**: `"""A dict that rejects modifications after freeze()."""`

**测试**: `test_frozen_dict_docstring_note`

## Deep Manual Findings

### Info — dead code: StaticYamlToolProvider 无引用

`grep` 全项目确认 `StaticYamlToolProvider` 仅在自身定义文件中出现，无任何 import 或使用。该文件可能是早期原型或为未来扩展预留。

## Verified Claims

按文档章节列出所有验证通过的声称：

### §2.1 架构总览
- [x] FileWatcher 存在于 `arf/resources/file_watcher.py`
- [x] ToolProvider 存在于 `arf/resources/providers/tool_provider.py`
- [x] SkillProvider 存在于 `arf/resources/providers/skill_provider.py`
- [x] ModelProvider 存在于 `arf/resources/providers/model_provider.py`
- [x] ResourceCache 存在，具有 kernel + dynamic 分离
- [x] ResourceResolver 存在 — 统一查询入口
- [x] 四层架构（监听、解析、缓存、合并）
- [x] `reload_dynamic()` → `invalidate_dynamic()` → `dynamic.clear()` 流程
- [x] kernel 在 dynamic 失效时不受影响

### §2.2 三个 Provider
- [x] ToolProvider 使用 `importlib.util.spec_from_file_location` 动态导入
- [x] SkillProvider 扫描 YAML 文件，纯声明无函数加载
- [x] SkillProvider.list() 合并 kernel + dynamic
- [x] ModelProvider 使用 `activation` 字段分离 kernel/dynamic
- [x] 三者有一致的接口: `list_kernel()` / `list_dynamic()` / `invalidate_dynamic()`
- [x] `list_kernel()` 和 `list_dynamic()` 返回 list

### §2.3 内核/动态分离
- [x] `_FrozenDict` 在 freeze 后拒绝 `__setitem__` / `__delitem__` / `pop` / `popitem` / `clear`
- [x] ToolConfig.activation 默认值为 `"kernel"`
- [x] SkillConfig.activation 默认值为 `"discoverable"`
- [x] ModelConfig.activation 默认值为 `"discoverable"`
- [x] `all_items()` 合并 kernel + dynamic（dynamic 覆盖同名 kernel 键）
- [x] `has_kernel()` / `has_dynamic()` 查询方法存在

### §2.4 ResourceResolver
- [x] 构造函数参数: `tool_provider`, `skill_provider`, `model_provider`, `agent_yaml_overrides`
- [x] `get_tool_definitions()` 返回 `list[ToolDefinition]`
- [x] `reload_dynamic()` 对标 `systemctl daemon-reload`
- [x] `generate_config()` 对标 `regedit /export` — 返回 dict
- [x] `get_skill_definitions()` / `get_model_definitions()` 存在
- [x] agent.yaml 覆盖优先于文件系统（override-only entries 追加）
- [x] 两种来源都空返回空列表
- [x] `set_plugin_provider()` 方法存在
- [x] DefaultToolResolver 保留旧构造函数签名 (`providers`, `retriever`, `backend`)
- [x] DefaultToolResolver 内部委托给 ResourceResolver

### §2.5 FileWatcher
- [x] 默认 `poll_interval = 5.0` 秒
- [x] inotify 监听: `IN_CLOSE_WRITE | IN_DELETE | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE`
- [x] 4096 字节缓冲区
- [x] `inotify_init()` 失败时静默切换到轮询
- [x] `add_watch()` / `remove_watch()` / `start()` / `stop()` 方法存在
- [x] `_seed_mtimes()` 跳过 `.git` 目录
- [x] Linux 使用 inotify loop，非 Linux 使用 poll loop
- [x] `select.select` 超时使用 `poll_interval`
- [x] inotify_init 失败时将 `_task` 重置为 `_poll_loop`

### §2.6 配置
- [x] `ReloadConfig.watch` 默认值为 `True`
- [x] `ReloadConfig.poll_interval` 默认值为 `5.0`
- [x] ReloadConfig 正确 wiring 到 FileWatcher（`base.py:439`）

### 其他
- [x] `arf.resources.__init__` 导出 ResourceResolver, DefaultToolResolver, ToolProvider, FunctionBackend
- [x] `providers.__init__` 导出全部三个 Provider
- [x] ResourceCache.kernel 是 `_FrozenDict` 类型
- [x] ResourceCache.dynamic 是普通 dict
- [x] FunctionBackend 支持 `rollback_fn` 参数
- [x] ToolProvider 支持 kernel_rollbacks 和 rollbacks

## Test Suite
- **文件**: `tests/fact_check/test_resource_registry.py`
- **结构**: 17 个 TestClass，84 个测试方法
- **覆盖**: 文档 §2.1-§2.6 全部章节
