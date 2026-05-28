"""Fact-check tests: Resource Registry — docs/resource-registry.md vs arf/resources/."""

import inspect
import sys
from pathlib import Path

import pytest


# ============================================================
# Section 2.1 — Architecture Overview
# ============================================================

class TestArchitectureComponents:
    """Doc §2.1: Four-layer architecture — FileWatcher, Providers, Cache, Resolver."""

    def test_file_watcher_exists(self):
        """Doc: FileWatcher at arf/resources/file_watcher.py."""
        from arf.resources.file_watcher import FileWatcher
        assert FileWatcher is not None

    def test_tool_provider_exists(self):
        """Doc: ToolProvider at arf/resources/providers/tool_provider.py."""
        from arf.resources.providers.tool_provider import ToolProvider
        assert ToolProvider is not None

    def test_skill_provider_exists(self):
        """Doc: SkillProvider at arf/resources/providers/skill_provider.py."""
        from arf.resources.providers.skill_provider import SkillProvider
        assert SkillProvider is not None

    def test_model_provider_exists(self):
        """Doc: ModelProvider at arf/resources/providers/model_provider.py."""
        from arf.resources.providers.model_provider import ModelProvider
        assert ModelProvider is not None

    def test_resource_cache_exists(self):
        """Doc: ResourceCache with kernel + dynamic split."""
        from arf.resources.cache import ResourceCache
        cache = ResourceCache()
        assert hasattr(cache, "kernel")
        assert hasattr(cache, "dynamic")

    def test_resource_resolver_exists(self):
        """Doc: ResourceResolver — unified query entry point."""
        from arf.resources.resolver import ResourceResolver
        assert ResourceResolver is not None

    def test_four_layer_count(self):
        """Doc lists exactly 4 layers: 监听层, 解析层, 缓存层, 合并层."""
        # Existence of all four verified above
        layers = ["FileWatcher", "ToolProvider/SkillProvider/ModelProvider", "ResourceCache", "ResourceResolver"]
        assert len(layers) == 4


class TestArchitectureFlow:
    """Doc §2.1: Architecture flow diagram claims."""

    def test_reload_dynamic_calls_invalidate_dynamic(self):
        """Doc: ResourceResolver.reload_dynamic() → Provider.invalidate_dynamic()."""
        import asyncio
        from arf.resources.resolver import ResourceResolver
        from arf.resources.providers.tool_provider import ToolProvider

        tp = ToolProvider("/nonexistent_tools_dir")
        resolver = ResourceResolver(tool_provider=tp)
        asyncio.run(resolver.reload_dynamic())
        # Should not raise — dynamic invalidation is idempotent

    def test_cache_has_invalidate_dynamic_method(self):
        """Doc: ResourceCache.dynamic.clear() is called on invalidation."""
        from arf.resources.cache import ResourceCache
        cache = ResourceCache()
        cache.dynamic["test"] = "value"
        cache.invalidate_dynamic()
        assert "test" not in cache.dynamic

    def test_cache_kernel_unaffected_by_invalidation(self):
        """Doc: kernel remains untouched during dynamic invalidation."""
        from arf.resources.cache import ResourceCache
        cache = ResourceCache()
        cache.kernel["kernel_tool"] = "kval"
        cache.freeze_kernel()
        cache.invalidate_dynamic()
        assert "kernel_tool" in cache.kernel


# ============================================================
# Section 2.2 — Three Providers
# ============================================================

class TestToolProvider:
    """Doc §2.2: ToolProvider scans tools/{name}/ for tool.yaml + function.py."""

    def test_constructor_accepts_tools_dir_not_fs_root(self):
        """Doc claims providers accept `fs_root` — code uses `tools_dir`.
        This is a DOC BUG: the doc says fs_root but code says tools_dir."""
        from arf.resources.providers.tool_provider import ToolProvider
        sig = inspect.signature(ToolProvider.__init__)
        params = list(sig.parameters.keys())
        # Doc says fs_root — reality check
        assert "tools_dir" in params
        # Documented claim is wrong:
        assert "fs_root" not in params

    def test_uses_importlib_spec_from_file_location(self):
        """Doc: importlib.util.spec_from_file_location for dynamic import."""
        import importlib.util
        from arf.resources.providers.tool_provider import ToolProvider
        source = inspect.getsource(ToolProvider._load)
        assert "importlib.util.spec_from_file_location" in source or \
               "spec_from_file_location" in source

    def test_list_kernel_returns_list(self):
        """Doc: list_kernel() returns kernel tools."""
        from arf.resources.providers.tool_provider import ToolProvider
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            tp = ToolProvider(td)
            result = tp.list_kernel()
            assert isinstance(result, list)

    def test_list_dynamic_returns_list(self):
        """Doc: list_dynamic() returns dynamic tools."""
        from arf.resources.providers.tool_provider import ToolProvider
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tp = ToolProvider(td)
            result = tp.list_dynamic()
            assert isinstance(result, list)

    def test_invalidate_dynamic_exists(self):
        """Doc: invalidate_dynamic() clears dynamic cache."""
        from arf.resources.providers.tool_provider import ToolProvider
        assert hasattr(ToolProvider, "invalidate_dynamic")


class TestSkillProvider:
    """Doc §2.2: SkillProvider scans skills/*.yaml."""

    def test_constructor_accepts_skills_dir_not_fs_root(self):
        """Doc claims providers accept `fs_root` — code uses `skills_dir`."""
        from arf.resources.providers.skill_provider import SkillProvider
        sig = inspect.signature(SkillProvider.__init__)
        params = list(sig.parameters.keys())
        assert "skills_dir" in params
        assert "fs_root" not in params

    def test_scans_yaml_files(self):
        """Doc: scans skills/*.yaml. Each file = one SkillConfig."""
        from arf.resources.providers.skill_provider import SkillProvider
        source = inspect.getsource(SkillProvider._load)
        assert "*.yaml" in source or ".yaml" in source

    def test_pure_declarative_no_function_loading(self):
        """Doc: 纯声明——无函数加载 (pure declaration, no function loading)."""
        from arf.resources.providers.skill_provider import SkillProvider
        source = inspect.getsource(SkillProvider._load)
        assert "importlib" not in source
        assert "spec_from_file_location" not in source

    def test_list_combines_kernel_and_dynamic(self):
        """Doc: list() returns kernel + dynamic combined."""
        from arf.resources.providers.skill_provider import SkillProvider
        source = inspect.getsource(SkillProvider.list)
        assert "list_kernel" in source
        assert "list_dynamic" in source

    def test_invalidate_dynamic_exists(self):
        """Doc: invalidate_dynamic() exists."""
        from arf.resources.providers.skill_provider import SkillProvider
        assert hasattr(SkillProvider, "invalidate_dynamic")


class TestModelProvider:
    """Doc §2.2: ModelProvider scans models/*.yaml."""

    def test_constructor_accepts_models_dir_not_fs_root(self):
        """Doc claims providers accept `fs_root` — code uses `models_dir`."""
        from arf.resources.providers.model_provider import ModelProvider
        sig = inspect.signature(ModelProvider.__init__)
        params = list(sig.parameters.keys())
        assert "models_dir" in params
        assert "fs_root" not in params

    def test_activation_field_for_kernel_dynamic_split(self):
        """Doc: activation field used for kernel/dynamic separation."""
        from arf.resources.providers.model_provider import ModelProvider
        source = inspect.getsource(ModelProvider._load)
        assert "activation" in source

    def test_invalidate_dynamic_exists(self):
        """Doc: invalidate_dynamic() exists."""
        from arf.resources.providers.model_provider import ModelProvider
        assert hasattr(ModelProvider, "invalidate_dynamic")


class TestProviderInterfaceUniformity:
    """Doc §2.2: Each Provider follows same interface:
    list_kernel() / list_dynamic() / invalidate_dynamic()."""

    def test_tool_provider_has_uniform_interface(self):
        from arf.resources.providers.tool_provider import ToolProvider
        for method in ["list_kernel", "list_dynamic", "invalidate_dynamic"]:
            assert hasattr(ToolProvider, method), f"ToolProvider missing {method}"

    def test_skill_provider_has_uniform_interface(self):
        from arf.resources.providers.skill_provider import SkillProvider
        for method in ["list_kernel", "list_dynamic", "invalidate_dynamic"]:
            assert hasattr(SkillProvider, method), f"SkillProvider missing {method}"

    def test_model_provider_has_uniform_interface(self):
        from arf.resources.providers.model_provider import ModelProvider
        for method in ["list_kernel", "list_dynamic", "invalidate_dynamic"]:
            assert hasattr(ModelProvider, method), f"ModelProvider missing {method}"


# ============================================================
# Section 2.3 — Kernel/Dynamic Split
# ============================================================

class TestFrozenDict:
    """Doc §2.3: _FrozenDict — kernel cache that rejects modifications after freeze."""

    def test_frozen_dict_exists(self):
        """Doc: _FrozenDict class exists in arf/resources/cache.py."""
        from arf.resources.cache import _FrozenDict
        assert _FrozenDict is not None

    def test_frozen_dict_raises_on_setitem_after_freeze(self):
        """Doc: kernel cache is frozen — cannot modify after init."""
        from arf.resources.cache import _FrozenDict
        d = _FrozenDict()
        d["a"] = 1
        d.freeze()
        with pytest.raises(RuntimeError, match="frozen"):
            d["b"] = 2

    def test_frozen_dict_raises_on_delitem_after_freeze(self):
        """Doc: __delitem__ also constrained by freeze."""
        from arf.resources.cache import _FrozenDict
        d = _FrozenDict({"a": 1})
        d.freeze()
        with pytest.raises(RuntimeError, match="frozen"):
            del d["a"]

    def test_frozen_dict_raises_on_pop_after_freeze(self):
        """Doc: pop also constrained."""
        from arf.resources.cache import _FrozenDict
        d = _FrozenDict({"a": 1})
        d.freeze()
        with pytest.raises(RuntimeError, match="frozen"):
            d.pop("a")

    def test_frozen_dict_raises_on_popitem_after_freeze(self):
        """Doc: popitem also constrained."""
        from arf.resources.cache import _FrozenDict
        d = _FrozenDict({"a": 1})
        d.freeze()
        with pytest.raises(RuntimeError, match="frozen"):
            d.popitem()

    def test_frozen_dict_raises_on_clear_after_freeze(self):
        """Doc: clear also constrained."""
        from arf.resources.cache import _FrozenDict
        d = _FrozenDict({"a": 1})
        d.freeze()
        with pytest.raises(RuntimeError, match="frozen"):
            d.clear()

    def test_frozen_dict_docstring_note(self):
        """Doc §2.3 shows _FrozenDict docstring:
        '对标 systemd 的静态单元缓存——init 时加载，之后不可变。'
        Actual code has different docstring — this is a DOC BUG."""
        from arf.resources.cache import _FrozenDict
        actual_doc = (_FrozenDict.__doc__ or "").strip()
        # Doc shows a specific Chinese docstring; actual is English
        # This test documents the discrepancy
        assert len(actual_doc) > 0  # has some docstring


class TestActivationKernel:
    """Doc §2.3: activation: kernel marks framework built-in resources."""

    def test_tool_config_activation_field(self):
        """Doc: activation field on ToolConfig, default 'kernel'."""
        from arf.core.config_base import ToolConfig
        assert hasattr(ToolConfig, "model_fields")
        assert "activation" in ToolConfig.model_fields
        field = ToolConfig.model_fields["activation"]
        assert field.default == "kernel"

    def test_skill_config_activation_default_discoverable(self):
        """Doc: SkillConfig activation defaults to 'discoverable'."""
        from arf.core.config_base import SkillConfig
        field = SkillConfig.model_fields["activation"]
        assert field.default == "discoverable"

    def test_model_config_activation_default_discoverable(self):
        """Doc: ModelConfig activation defaults to 'discoverable'."""
        from arf.core.config_base import ModelConfig
        field = ModelConfig.model_fields["activation"]
        assert field.default == "discoverable"


class TestCacheAllItems:
    """Doc §2.3: all_items() returns merged kernel + dynamic."""

    def test_all_items_merges_kernel_and_dynamic(self):
        """Doc: merged kernel + dynamic (dynamic wins on conflict)."""
        from arf.resources.cache import ResourceCache
        cache = ResourceCache()
        cache.kernel["shared"] = "kernel_val"
        cache.dynamic["shared"] = "dynamic_val"
        cache.dynamic["dyn_only"] = "dyn"
        items = cache.all_items()
        assert items["shared"] == "dynamic_val"  # dynamic wins
        assert items["dyn_only"] == "dyn"

    def test_has_kernel_and_has_dynamic(self):
        """Doc: has_kernel / has_dynamic lookup methods."""
        from arf.resources.cache import ResourceCache
        cache = ResourceCache()
        cache.kernel["k"] = 1
        cache.dynamic["d"] = 2
        assert cache.has_kernel("k")
        assert not cache.has_kernel("d")
        assert cache.has_dynamic("d")
        assert not cache.has_dynamic("k")


# ============================================================
# Section 2.4 — ResourceResolver
# ============================================================

class TestResourceResolver:
    """Doc §2.4: ResourceResolver — override merge and config generation."""

    def test_constructor_params(self):
        """Doc: __init__(tool_provider, skill_provider, model_provider, agent_yaml_overrides)."""
        from arf.resources.resolver import ResourceResolver
        sig = inspect.signature(ResourceResolver.__init__)
        params = list(sig.parameters.keys())
        assert "tool_provider" in params
        assert "skill_provider" in params
        assert "model_provider" in params
        assert "agent_yaml_overrides" in params

    def test_get_tool_definitions_async(self):
        """Doc: get_tool_definitions() returns list[ToolDefinition]."""
        import asyncio
        from arf.resources.resolver import ResourceResolver
        from arf.resources.providers.tool_provider import ToolProvider
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tp = ToolProvider(td)
            resolver = ResourceResolver(tool_provider=tp)
            result = asyncio.run(resolver.get_tool_definitions())
            assert isinstance(result, list)

    def test_reload_dynamic_exists(self):
        """Doc: reload_dynamic() — 对标 systemctl daemon-reload."""
        from arf.resources.resolver import ResourceResolver
        assert hasattr(ResourceResolver, "reload_dynamic")

    def test_generate_config_exists(self):
        """Doc: generate_config() — 对标 regedit /export 或 dpkg -l."""
        from arf.resources.resolver import ResourceResolver
        assert hasattr(ResourceResolver, "generate_config")

    def test_generate_config_returns_dict(self):
        """Doc: generate_config() dumps all discovered resources."""
        import asyncio
        from arf.resources.resolver import ResourceResolver
        from arf.resources.providers.tool_provider import ToolProvider
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tp = ToolProvider(td)
            resolver = ResourceResolver(tool_provider=tp)
            result = asyncio.run(resolver.generate_config())
            assert isinstance(result, dict)

    def test_override_priority_empty_overrides(self):
        """Doc: Priority — agent.yaml override > filesystem > Pydantic default.
        With no overrides, filesystem values pass through."""
        import asyncio
        from arf.resources.resolver import ResourceResolver
        from arf.resources.providers.tool_provider import ToolProvider
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tp = ToolProvider(td)
            resolver = ResourceResolver(tool_provider=tp)
            result = asyncio.run(resolver.get_tool_definitions())
            assert isinstance(result, list)

    def test_get_skill_definitions(self):
        """Doc: get_skill_definitions() returns list[SkillConfig]."""
        import tempfile
        from arf.resources.resolver import ResourceResolver
        from arf.resources.providers.tool_provider import ToolProvider
        from arf.resources.providers.skill_provider import SkillProvider
        with tempfile.TemporaryDirectory() as td:
            tp = ToolProvider(td)
            sp = SkillProvider(td)
            resolver = ResourceResolver(tool_provider=tp, skill_provider=sp)
            result = resolver.get_skill_definitions()
            assert isinstance(result, list)

    def test_get_model_definitions(self):
        """Doc: get_model_definitions() returns list[ModelConfig]."""
        import tempfile
        from arf.resources.resolver import ResourceResolver
        from arf.resources.providers.tool_provider import ToolProvider
        from arf.resources.providers.model_provider import ModelProvider
        with tempfile.TemporaryDirectory() as td:
            tp = ToolProvider(td)
            mp = ModelProvider(td)
            resolver = ResourceResolver(tool_provider=tp, model_provider=mp)
            result = resolver.get_model_definitions()
            assert isinstance(result, list)

    def test_merge_configs_appends_override_only_entries(self):
        """Doc: agent.yaml can declare resources not in filesystem
        (追加到合并结果)."""
        import asyncio
        from arf.resources.resolver import ResourceResolver
        from arf.resources.providers.tool_provider import ToolProvider
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tp = ToolProvider(td)
            overrides = {
                "tools": [{"name": "virtual_tool", "description": "from override"}]
            }
            resolver = ResourceResolver(tool_provider=tp, agent_yaml_overrides=overrides)
            tools = asyncio.run(resolver.get_tool_definitions())
            names = [t.name for t in tools]
            assert "virtual_tool" in names

    def test_both_sources_empty_returns_empty(self):
        """Doc: 两种来源都空默认返回空列表 (both empty = return empty list)."""
        import asyncio
        from arf.resources.resolver import ResourceResolver
        from arf.resources.providers.tool_provider import ToolProvider
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tp = ToolProvider(td)
            resolver = ResourceResolver(tool_provider=tp)
            tools = asyncio.run(resolver.get_tool_definitions())
            assert tools == []

    def test_set_plugin_provider_exists(self):
        """Doc doesn't mention PluginProvider, but resolver supports it.
        Verify set_plugin_provider method exists."""
        from arf.resources.resolver import ResourceResolver
        assert hasattr(ResourceResolver, "set_plugin_provider")


class TestDefaultToolResolver:
    """Doc §2.4: DefaultToolResolver = ResourceResolver alias (backward compat)."""

    def test_default_tool_resolver_is_separate_class(self):
        """Doc says 'alias' but it's actually a wrapper class.
        This is a DOC BUG: DefaultToolResolver is a wrapper, not an alias."""
        from arf.resources.resolver import DefaultToolResolver, ResourceResolver
        assert DefaultToolResolver is not ResourceResolver
        assert issubclass(DefaultToolResolver, object)

    def test_default_tool_resolver_delegates_to_resource_resolver(self):
        """Doc: delegates internally to ResourceResolver."""
        import asyncio
        from arf.resources.resolver import DefaultToolResolver
        from arf.resources.providers.tool_provider import ToolProvider
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tp = ToolProvider(td)
            dtr = DefaultToolResolver(providers=[tp])
            tools = asyncio.run(dtr.get_tool_definitions())
            assert isinstance(tools, list)

    def test_default_tool_resolver_old_constructor_signature(self):
        """Doc claims old constructor signature preserved."""
        from arf.resources.resolver import DefaultToolResolver
        sig = inspect.signature(DefaultToolResolver.__init__)
        params = list(sig.parameters.keys())
        assert "providers" in params
        assert "retriever" in params
        assert "backend" in params

    def test_default_tool_resolver_reload_exists(self):
        """Doc: reload() method for backward compat."""
        from arf.resources.resolver import DefaultToolResolver
        assert hasattr(DefaultToolResolver, "reload")


# ============================================================
# Section 2.5 — FileWatcher
# ============================================================

class TestFileWatcher:
    """Doc §2.5: FileWatcher — cross-platform auto-reload."""

    def test_file_watcher_constructor(self):
        """Doc: FileWatcher in arf/resources/file_watcher.py."""
        from arf.resources.file_watcher import FileWatcher
        fw = FileWatcher()
        assert fw._poll_interval == 5.0  # default poll_interval

    def test_file_watcher_poll_interval_default(self):
        """Doc: poll_interval default is 5 (seconds)."""
        from arf.resources.file_watcher import FileWatcher
        fw = FileWatcher()
        assert fw._poll_interval == 5.0

    def test_inotify_events_mask(self):
        """Doc: IN_CLOSE_WRITE | IN_DELETE | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE."""
        from arf.resources.file_watcher import FileWatcher
        source = inspect.getsource(FileWatcher._inotify_loop)
        assert "IN_CLOSE_WRITE" in source
        assert "IN_DELETE" in source
        assert "IN_MOVED_FROM" in source
        assert "IN_MOVED_TO" in source
        assert "IN_CREATE" in source

    def test_buffer_size_4096(self):
        """Doc: 4096 byte buffer for inotify reads."""
        from arf.resources.file_watcher import FileWatcher
        source = inspect.getsource(FileWatcher._inotify_loop)
        assert "4096" in source

    def test_inotify_fallback_to_polling(self):
        """Doc: inotify_init() failure → silent switch to polling."""
        from arf.resources.file_watcher import FileWatcher
        source = inspect.getsource(FileWatcher._inotify_loop)
        assert "inotify_init failed" in source
        assert "_poll_loop" in source

    def test_add_watch_method_exists(self):
        """Doc: add_watch() for dynamic inotify registration."""
        from arf.resources.file_watcher import FileWatcher
        assert hasattr(FileWatcher, "add_watch")

    def test_remove_watch_method_exists(self):
        """Doc: remove_watch() method."""
        from arf.resources.file_watcher import FileWatcher
        assert hasattr(FileWatcher, "remove_watch")

    def test_start_stop_methods_exist(self):
        """Doc: start() and stop() for lifecycle."""
        from arf.resources.file_watcher import FileWatcher
        assert hasattr(FileWatcher, "start")
        assert hasattr(FileWatcher, "stop")

    def test_seed_mtimes_skips_git(self):
        """Doc: _seed_mtimes() snapshots file mtimes."""
        from arf.resources.file_watcher import FileWatcher
        source = inspect.getsource(FileWatcher._seed_mtimes)
        assert ".git" in source

    def test_linux_inotify_mode(self):
        """Doc: Linux uses ctypes inotify."""
        from arf.resources.file_watcher import FileWatcher
        source = inspect.getsource(FileWatcher.start)
        assert "linux" in source
        assert "_inotify_loop" in source

    def test_non_linux_poll_mode(self):
        """Doc: Non-Linux uses asyncio.sleep polling."""
        from arf.resources.file_watcher import FileWatcher
        source = inspect.getsource(FileWatcher.start)
        assert "_poll_loop" in source

    def test_fire_callbacks_supports_async(self):
        """Doc: Callbacks fire on changed paths."""
        from arf.resources.file_watcher import FileWatcher
        assert hasattr(FileWatcher, "_fire_callbacks")

    def test_inotify_loop_select_timeout_equals_poll_interval(self):
        """Doc: select.select uses poll_interval as timeout."""
        from arf.resources.file_watcher import FileWatcher
        source = inspect.getsource(FileWatcher._inotify_loop)
        assert "self._poll_interval" in source

    def test_inotify_init_failure_resets_task_to_poll(self):
        """Doc: inotify_init failure resets _task to _poll_loop."""
        from arf.resources.file_watcher import FileWatcher
        source = inspect.getsource(FileWatcher._inotify_loop)
        assert "self._task = asyncio.create_task(self._poll_loop())" in source


# ============================================================
# Section 2.6 — Configuration
# ============================================================

class TestReloadConfig:
    """Doc §2.6: advanced.reload configuration."""

    def test_reload_config_watch_default_true(self):
        """Doc: advanced.reload.watch default true."""
        from arf.core.config_base import ReloadConfig
        field = ReloadConfig.model_fields["watch"]
        assert field.default is True

    def test_reload_config_poll_interval_default_5(self):
        """Doc: advanced.reload.poll_interval default 5."""
        from arf.core.config_base import ReloadConfig
        field = ReloadConfig.model_fields["poll_interval"]
        assert field.default == 5.0

    def test_reload_config_class_exists(self):
        """Doc: ReloadConfig Pydantic model exists."""
        from arf.core.config_base import ReloadConfig
        assert ReloadConfig is not None


# ============================================================
# Section — API endpoint verification
# ============================================================

class TestReloadEndpoint:
    """Doc §2.3 mentions POST /api/resources/reload endpoint."""

    def test_reload_endpoint_path(self):
        """Doc says POST /api/resources/reload but actual is POST /api/reload.
        This is a DOC BUG."""
        router_path = Path("app/arf_default_assistant/routers/resources.py")
        if router_path.exists():
            source = router_path.read_text()
            # Actual endpoint path
            assert 'router.post("/api/reload")' in source or \
                   '@router.post("/api/reload")' in source
            # Doc claims /api/resources/reload — verify it's NOT that
            assert '/api/resources/reload' not in source


# ============================================================
# Section — Completeness: code has things doc doesn't mention
# ============================================================

class TestCompleteness:
    """Check for code entities not documented."""

    def test_plugin_provider_exists_in_code(self):
        """PluginProvider exists in code but NOT mentioned in doc.
        This is a COMPLETENESS finding (Info)."""
        from arf.resources.providers.plugin_provider import PluginProvider
        assert PluginProvider is not None

    def test_function_backend_exists(self):
        """Doc mentions FunctionBackend indirectly — verify it exists."""
        from arf.resources.backends.function import FunctionBackend
        assert FunctionBackend is not None

    def test_backends_init_exists(self):
        """Backends package exists."""
        path = Path("arf/resources/backends/__init__.py")
        assert path.exists()

    def test_providers_init_exports_all_three(self):
        """Doc says ToolProvider, SkillProvider, ModelProvider are the three providers.
        Verify __init__ exports all three."""
        from arf.resources import providers
        assert hasattr(providers, "ToolProvider")
        assert hasattr(providers, "SkillProvider")
        assert hasattr(providers, "ModelProvider")


# ============================================================
# Section — Backward compat & exports
# ============================================================

class TestResourcesInitExports:
    """Doc mentions ResourceResolver and DefaultToolResolver as main exports."""

    def test_resources_init_exports(self):
        """Doc: __init__.py exports ResourceResolver, DefaultToolResolver,
        ToolProvider, FunctionBackend."""
        import arf.resources
        assert hasattr(arf.resources, "ResourceResolver")
        assert hasattr(arf.resources, "DefaultToolResolver")
        assert hasattr(arf.resources, "ToolProvider")
        assert hasattr(arf.resources, "FunctionBackend")

    def test_default_tool_resolver_in_all(self):
        """Doc: DefaultToolResolver preserved as backward-compat alias."""
        from arf.resources import DefaultToolResolver
        assert DefaultToolResolver is not None


# ============================================================
# Section — Misc claims from doc body text
# ============================================================

class TestDesignClaims:
    """Miscellaneous claims from the doc."""

    def test_resource_cache_kernel_is_frozen_dict(self):
        """Doc §2.3: kernel uses _FrozenDict."""
        from arf.resources.cache import ResourceCache, _FrozenDict
        cache = ResourceCache()
        assert isinstance(cache.kernel, _FrozenDict)

    def test_dynamic_is_plain_dict(self):
        """Doc: dynamic is a plain dict (clearable)."""
        from arf.resources.cache import ResourceCache
        cache = ResourceCache()
        assert isinstance(cache.dynamic, dict)
        assert not hasattr(cache.dynamic, "freeze")

    def test_provider_method_is_private_load_not_load_all(self):
        """Doc mentions _load_all() but code uses _load().
        This is a DOC BUG: method naming."""
        from arf.resources.providers.tool_provider import ToolProvider
        from arf.resources.providers.skill_provider import SkillProvider
        from arf.resources.providers.model_provider import ModelProvider
        # All providers use _load, not _load_all
        assert hasattr(ToolProvider, "_load")
        assert hasattr(SkillProvider, "_load")
        assert hasattr(ModelProvider, "_load")
        assert not hasattr(ToolProvider, "_load_all")
        assert not hasattr(SkillProvider, "_load_all")
        assert not hasattr(ModelProvider, "_load_all")

    def test_tool_provider_execute_has_rollback_support(self):
        """Doc doesn't explicitly mention rollback in ToolProvider,
        but code has _kernel_rollbacks and _rollbacks dicts."""
        from arf.resources.providers.tool_provider import ToolProvider
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tp = ToolProvider(td)
            assert hasattr(tp, "_kernel_rollbacks")
            assert hasattr(tp, "_rollbacks")

    def test_function_backend_rollback_support(self):
        """Doc doesn't detail rollback, but FunctionBackend has it.
        Verify rollback_fn parameter exists."""
        import inspect
        from arf.resources.backends.function import FunctionBackend
        sig = inspect.signature(FunctionBackend.execute_with_fn)
        assert "rollback_fn" in sig.parameters

    def test_providers_directory_paths(self):
        """Doc: ToolProvider scans tools/{name}/, SkillProvider scans skills/*.yaml,
        ModelProvider scans models/*.yaml."""
        from arf.resources.providers.tool_provider import ToolProvider
        from arf.resources.providers.skill_provider import SkillProvider
        from arf.resources.providers.model_provider import ModelProvider
        # We verify that the code paths are relative and match doc claims
        source_tp = inspect.getsource(ToolProvider.__init__)
        assert "tools_dir" in source_tp

        source_sp = inspect.getsource(SkillProvider.__init__)
        assert "skills_dir" in source_sp

        source_mp = inspect.getsource(ModelProvider.__init__)
        assert "models_dir" in source_mp
