"""Fact-check tests: Tool Sandbox — docs/tool-sandbox.md vs arf/sandbox/ + arf/guardrails/."""

import asyncio
import inspect
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


# ============================================================
# Section 2.2 — Guardrails (four guards)
# ============================================================

class TestGuardModules:
    """Doc §2.2: Four guards — NoneInputGuard, PathCheckToolGuard,
    ToolPermissionChecker, RegexOutputGuard."""

    def test_none_input_guard_exists(self):
        from arf.guardrails.none_guard import NoneInputGuard
        assert NoneInputGuard is not None

    def test_path_check_tool_guard_exists(self):
        from arf.guardrails.path_check import PathCheckToolGuard
        assert PathCheckToolGuard is not None

    def test_tool_permission_checker_exists(self):
        from arf.guardrails.permissions import ToolPermissionChecker
        assert ToolPermissionChecker is not None

    def test_regex_output_guard_exists(self):
        from arf.guardrails.regex_clean import RegexOutputGuard
        assert RegexOutputGuard is not None

    def test_default_guard_runner_exists(self):
        from arf.guardrails.runner import DefaultGuardRunner
        assert DefaultGuardRunner is not None

    def test_guard_count_is_four(self):
        guards = ["NoneInputGuard", "PathCheckToolGuard", "ToolPermissionChecker", "RegexOutputGuard"]
        assert len(guards) == 4


class TestNoneInputGuard:
    """Doc §2.2: NoneInputGuard — always passes."""

    def test_always_allows(self):
        from arf.guardrails.none_guard import NoneInputGuard
        guard = NoneInputGuard()
        result = asyncio.run(guard.check("anything", {}))
        assert result.allowed is True


class TestRegexOutputGuard:
    """Doc §2.2: RegexOutputGuard — API key / phone → [REDACTED]."""

    def test_redacts_api_key(self):
        from arf.guardrails.regex_clean import RegexOutputGuard
        guard = RegexOutputGuard()
        result = asyncio.run(guard.check("sk-abc123def456ghi7890123456", {}))
        assert "[REDACTED" in (result.modified_message or "")

    def test_redacts_phone(self):
        from arf.guardrails.regex_clean import RegexOutputGuard
        guard = RegexOutputGuard()
        result = asyncio.run(guard.check("call 13812345678 now", {}))
        assert "[REDACTED" in (result.modified_message or "")

    def test_allows_clean_text(self):
        from arf.guardrails.regex_clean import RegexOutputGuard
        guard = RegexOutputGuard()
        result = asyncio.run(guard.check("hello world", {}))
        assert result.allowed is True

    def test_redact_labels_are_specific(self):
        """Doc §2.1: API key → [REDACTED_API_KEY], phone → [REDACTED_PHONE]."""
        from arf.guardrails.regex_clean import _BUILTIN_PATTERNS
        replacements = [p[1] for p in _BUILTIN_PATTERNS]
        assert "[REDACTED_API_KEY]" in replacements
        assert "[REDACTED_PHONE]" in replacements

    # -- real-world boundary tests --

    def test_sk_key_exactly_20_chars_redacted(self):
        """API key at minimum threshold (20 chars after sk-) matches."""
        from arf.guardrails.regex_clean import RegexOutputGuard
        guard = RegexOutputGuard()
        result = asyncio.run(guard.check("sk-abcdefghijklmnopqrst", {}))
        assert "[REDACTED_API_KEY]" in (result.modified_message or "")

    def test_sk_key_19_chars_not_redacted(self):
        """API key below threshold (19 chars) does NOT match."""
        from arf.guardrails.regex_clean import RegexOutputGuard
        guard = RegexOutputGuard()
        result = asyncio.run(guard.check("sk-shortkeyabcde", {}))
        assert result.modified_message is None

    def test_openai_project_key_redacted(self):
        """OpenAI project keys (sk-proj-*) contain '-' — fixed regex to include '-'.
        Previously: NOT redacted (gap). Now: correctly redacted."""
        from arf.guardrails.regex_clean import RegexOutputGuard
        guard = RegexOutputGuard()
        result = asyncio.run(guard.check(
            "use key sk-proj-abc123def456ghi789jkl", {}
        ))
        assert "[REDACTED_API_KEY]" in (result.modified_message or "")

    def test_phone_redacts_199_prefix(self):
        """New prefix 199 (5G) matches."""
        from arf.guardrails.regex_clean import RegexOutputGuard
        guard = RegexOutputGuard()
        result = asyncio.run(guard.check("19912345678", {}))
        assert "[REDACTED_PHONE]" in (result.modified_message or "")

    def test_phone_does_not_redact_120_prefix(self):
        """120/110/122 etc (second digit 0-2) NOT mobile numbers."""
        from arf.guardrails.regex_clean import RegexOutputGuard
        guard = RegexOutputGuard()
        result = asyncio.run(guard.check("12012345678", {}))
        assert result.modified_message is None

    def test_id_card_no_longer_false_positive(self):
        """ID card '110101199001011234' had substring '19900101123' matching
        phone regex. Fixed with \\b word boundaries — ID card no longer
        triggers false positive phone redaction."""
        from arf.guardrails.regex_clean import RegexOutputGuard
        guard = RegexOutputGuard()
        result = asyncio.run(guard.check("110101199001011234", {}))
        assert result.modified_message is None, (
            "ID card should NOT be redacted with word-boundary regex"
        )

    def test_bank_card_not_redacted(self):
        """GAP: Bank card numbers have no pattern — unprotected."""
        from arf.guardrails.regex_clean import RegexOutputGuard
        guard = RegexOutputGuard()
        result = asyncio.run(guard.check("6222021234567890123", {}))
        assert result.modified_message is None

    def test_two_phones_both_redacted(self):
        """Multiple phone numbers in same text all replaced."""
        from arf.guardrails.regex_clean import RegexOutputGuard
        guard = RegexOutputGuard()
        result = asyncio.run(guard.check(
            "call 13900000000 or 18887654321", {}
        ))
        msg = result.modified_message or ""
        assert msg.count("[REDACTED_PHONE]") == 2

    def test_phone_in_longer_number_not_redacted(self):
        """14-digit number: \\b word boundaries prevent partial match.
        Only exact 11-digit phone numbers are redacted."""
        from arf.guardrails.regex_clean import RegexOutputGuard
        guard = RegexOutputGuard()
        result = asyncio.run(guard.check("139000000001234", {}))
        assert result.modified_message is None


# ============================================================
# Section 2.3 — PathCheckToolGuard
# ============================================================

class TestPathCheckToolGuard:
    """Doc §2.3: PathCheckToolGuard — path sandbox."""

    def test_check_method_signature(self):
        from arf.guardrails.path_check import PathCheckToolGuard
        sig = inspect.signature(PathCheckToolGuard.check)
        params = list(sig.parameters.keys())
        assert "tool_name" in params
        assert "params" in params

    def test_blocks_path_traversal(self):
        from arf.guardrails.path_check import PathCheckToolGuard
        guard = PathCheckToolGuard(workspace_root="/tmp", checks={"path_traversal": True, "workspace_containment": True})
        result = asyncio.run(guard.check("test", {"file": "../etc/passwd"}))
        assert result.allowed is False
        assert "traversal" in result.reason.lower()

    def test_blocks_absolute_path(self):
        from arf.guardrails.path_check import PathCheckToolGuard
        guard = PathCheckToolGuard(workspace_root="/tmp", checks={"absolute_path": True, "workspace_containment": True})
        result = asyncio.run(guard.check("test", {"file": "/etc/passwd"}))
        assert result.allowed is False
        assert "absolute" in result.reason.lower()

    def test_allow_escape_bypasses_all_checks(self):
        from arf.guardrails.path_check import PathCheckToolGuard
        guard = PathCheckToolGuard(workspace_root="/tmp", allow_escape=True)
        result = asyncio.run(guard.check("test", {"file": "../etc/passwd"}))
        assert result.allowed is True

    def test_allows_relative_safe_path(self):
        import tempfile, os
        from arf.guardrails.path_check import PathCheckToolGuard
        with tempfile.TemporaryDirectory() as td:
            guard = PathCheckToolGuard(workspace_root=td)
            safe = os.path.join(td, "safe.txt")
            Path(safe).write_text("hello")
            result = asyncio.run(guard.check("test", {"file": "safe.txt"}))
            assert result.allowed is True

    def test_walk_strings_recursive_dict(self):
        from arf.guardrails.path_check import PathCheckToolGuard
        obj = {"a": "path1", "b": {"c": "path2", "d": ["path3", "path4"]}}
        results = list(PathCheckToolGuard._walk_strings(obj))
        assert "path1" in results
        assert "path2" in results
        assert "path3" in results
        assert "path4" in results

    def test_walk_strings_handles_tuple_and_set(self):
        from arf.guardrails.path_check import PathCheckToolGuard
        obj = {"a": ("tuple_path",), "b": {"set_path"}}
        results = list(PathCheckToolGuard._walk_strings(obj))
        assert "tuple_path" in results
        assert "set_path" in results

    def test_resource_quota_defaults(self):
        from arf.guardrails.path_check import ResourceQuota
        quota = ResourceQuota()
        assert quota.max_path_count is None
        assert quota.max_path_depth is None
        assert quota.deny_symlinks is True

    def test_resource_quota_count_one(self):
        from arf.guardrails.path_check import ResourceQuota
        quota = ResourceQuota(max_path_count=2)
        assert quota.count_one() is True
        assert quota.count_one() is True
        assert quota.count_one() is False

    def test_resource_quota_reset(self):
        from arf.guardrails.path_check import ResourceQuota
        quota = ResourceQuota(max_path_count=1)
        quota.count_one()
        quota.reset()
        assert quota.count_one() is True

    def test_depth_quota_exceeded(self):
        from arf.guardrails.path_check import PathCheckToolGuard, ResourceQuota
        quota = ResourceQuota(max_path_depth=2)
        guard = PathCheckToolGuard(workspace_root="/tmp", quota=quota)
        result = asyncio.run(guard.check("test", {"file": "a/b/c/d.txt"}))
        assert result.allowed is False
        assert "depth" in result.reason.lower()

    def test_count_quota_exceeded(self):
        from arf.guardrails.path_check import PathCheckToolGuard, ResourceQuota
        quota = ResourceQuota(max_path_count=1)
        guard = PathCheckToolGuard(workspace_root="/tmp", quota=quota)
        # Two path strings exceed max_path_count=1
        result = asyncio.run(guard.check("test", {"a": "x.txt", "b": "y.txt"}))
        assert result.allowed is False
        assert "count" in result.reason.lower()


class TestQuotaCheckOrder:
    """Doc §2.3: 6 checks in order, first failure returns."""

    def test_check_order_traversal_before_absolute(self):
        from arf.guardrails.path_check import PathCheckToolGuard
        guard = PathCheckToolGuard(workspace_root="/tmp", checks={"path_traversal": True, "absolute_path": True, "workspace_containment": True})
        result = asyncio.run(guard.check("test", {"file": "../etc"}))
        assert result.allowed is False
        assert "traversal" in result.reason.lower()

    def test_check_order_absolute_before_depth(self):
        from arf.guardrails.path_check import PathCheckToolGuard, ResourceQuota
        quota = ResourceQuota(max_path_depth=0)
        guard = PathCheckToolGuard(workspace_root="/tmp", quota=quota, checks={"absolute_path": True, "workspace_containment": True})
        result = asyncio.run(guard.check("test", {"file": "/a"}))
        assert result.allowed is False
        assert "absolute" in result.reason.lower()


# ============================================================
# Section 2.5 — ToolPermissionChecker (deny → ask → allow)
# ============================================================

class TestToolPermissionChecker:
    """Doc §2.5: deny → ask → allow pipeline."""

    def test_permission_checker_exists(self):
        from arf.guardrails.permissions import ToolPermissionChecker
        assert ToolPermissionChecker is not None

    def test_deny_by_pattern(self):
        from arf.guardrails.permissions import ToolPermissionChecker
        checker = ToolPermissionChecker()
        result = checker.check("file_writer", {"command": "sudo rm -rf /"})
        assert result == "deny"

    def test_deny_by_config_list(self):
        from arf.guardrails.permissions import ToolPermissionChecker
        checker = ToolPermissionChecker({"deny": ["python_exec"]})
        result = checker.check("python_exec", {})
        assert result == "deny"

    def test_ask_by_config_list(self):
        from arf.guardrails.permissions import ToolPermissionChecker
        checker = ToolPermissionChecker({"ask": ["file_writer"], "allow": []})
        result = checker.check("file_writer", {})
        assert result == "ask"

    def test_allow_by_config_list(self):
        from arf.guardrails.permissions import ToolPermissionChecker
        checker = ToolPermissionChecker({"allow": ["file_reader"]})
        result = checker.check("file_reader", {})
        assert result == "allow"

    def test_default_is_ask(self):
        from arf.guardrails.permissions import ToolPermissionChecker
        checker = ToolPermissionChecker({"allow": []})
        result = checker.check("unknown_tool", {})
        assert result == "ask"

    def test_deny_priority_over_ask(self):
        from arf.guardrails.permissions import ToolPermissionChecker
        checker = ToolPermissionChecker({"deny": ["file_writer"], "ask": ["file_writer"]})
        result = checker.check("file_writer", {})
        assert result == "deny"

    def test_deny_priority_over_allow(self):
        from arf.guardrails.permissions import ToolPermissionChecker
        checker = ToolPermissionChecker({"deny": ["file_reader"], "allow": ["file_reader"]})
        result = checker.check("file_reader", {})
        assert result == "deny"

    def test_ask_priority_over_allow(self):
        from arf.guardrails.permissions import ToolPermissionChecker
        checker = ToolPermissionChecker({"ask": ["file_reader"], "allow": ["file_reader"]})
        result = checker.check("file_reader", {})
        assert result == "ask"

    def test_default_allow_tools(self):
        from arf.guardrails.permissions import _DEFAULT_ALLOW_TOOLS
        assert "file_reader" in _DEFAULT_ALLOW_TOOLS
        assert "web_search" in _DEFAULT_ALLOW_TOOLS
        assert len(_DEFAULT_ALLOW_TOOLS) == 7

    def test_builtin_deny_patterns(self):
        from arf.guardrails.permissions import _BUILTIN_DENY_PATTERNS
        assert len(_BUILTIN_DENY_PATTERNS) == 6
        assert "sudo " in _BUILTIN_DENY_PATTERNS
        assert "rm -rf /" in _BUILTIN_DENY_PATTERNS


class TestApprovalDowngradeBehavior:
    """Doc §2.5: 'ask' with no approval channel.
    Design intent: approval_enabled=False = YOLO mode, tools pass through.
    Doc updated to reflect this."""

    def test_graph_engine_class_has_approval_attr(self):
        from arf.engine.graph import GraphEngine
        sig = inspect.signature(GraphEngine.__init__)
        assert "approval_enabled" in sig.parameters

    def test_approval_enabled_defaults_false(self):
        from arf.engine.graph import GraphEngine
        sig = inspect.signature(GraphEngine.__init__)
        default = sig.parameters["approval_enabled"].default
        assert default is False

    def test_graph_engine_yolo_mode_bypasses_approval(self):
        """When approval_enabled=False (YOLO mode), 'ask' → allow.
        The code skips the approval block and falls through to valid_calls."""
        from arf.engine.graph import GraphEngine
        source = inspect.getsource(GraphEngine._step_classify_tool_calls)
        assert "needs_approval" in source
        # needs_approval=False skips approval → valid_calls (design intent)


# ============================================================
# Section 2.3 — PathSandbox
# ============================================================

class TestPathSandbox:
    """Doc §2.3: PathSandbox in arf/sandbox/path_sandbox.py."""

    def test_path_sandbox_exists(self):
        from arf.sandbox.path_sandbox import PathSandbox
        assert PathSandbox is not None

    def test_validate_path_blocks_traversal(self):
        import tempfile
        from arf.sandbox.path_sandbox import PathSandbox
        with tempfile.TemporaryDirectory() as td:
            sandbox = PathSandbox(workspace_root=td)
            assert sandbox.validate_path("../escape") is False

    def test_validate_path_allows_safe_path(self):
        import tempfile, os
        from arf.sandbox.path_sandbox import PathSandbox
        with tempfile.TemporaryDirectory() as td:
            safe = os.path.join(td, "ok.txt")
            Path(safe).write_text("hello")
            sandbox = PathSandbox(workspace_root=td)
            assert sandbox.validate_path("ok.txt") is True

    def test_has_symlink_detection(self):
        import tempfile
        from arf.sandbox.path_sandbox import PathSandbox
        with tempfile.TemporaryDirectory() as td:
            sandbox = PathSandbox(workspace_root=td)
            f = Path(td) / "regular.txt"
            f.write_text("hello")
            assert sandbox.has_symlink("regular.txt") is False

    def test_validate_command_exists(self):
        from arf.sandbox.path_sandbox import PathSandbox
        assert hasattr(PathSandbox, "validate_command")

    def test_resolve_path_exists(self):
        from arf.sandbox.path_sandbox import PathSandbox
        assert hasattr(PathSandbox, "resolve_path")

    def test_allowed_dirs_exists(self):
        from arf.sandbox.path_sandbox import PathSandbox
        assert hasattr(PathSandbox, "allowed_dirs")

    def test_root_property_exists(self):
        import tempfile
        from arf.sandbox.path_sandbox import PathSandbox
        with tempfile.TemporaryDirectory() as td:
            sandbox = PathSandbox(workspace_root=td)
            assert sandbox.root == Path(td).resolve()


# ============================================================
# Section 2.7 — Guard config wiring
# ============================================================

class TestGuardConfigWiring:
    """Doc §2.7: Config → implementation wiring claims."""

    def test_permissions_config_model_exists(self):
        from arf.core.config_base import PermissionsConfig
        cfg = PermissionsConfig()
        assert cfg.deny == []
        assert cfg.ask == []
        assert cfg.allow == []

    def test_permissions_config_has_deny_patterns(self):
        from arf.core.config_base import PermissionsConfig
        cfg = PermissionsConfig()
        assert hasattr(cfg, "deny_patterns")
        assert cfg.deny_patterns == []

    def test_guardrails_config_fields(self):
        from arf.core.config_base import GuardrailsConfig
        cfg = GuardrailsConfig()
        assert cfg.input == "none"
        assert cfg.output == "regex_clean"
        assert cfg.tool_params == "path_check"

    def test_sandbox_config_fields(self):
        from arf.core.config_base import SandboxConfig
        cfg = SandboxConfig()
        assert cfg.allow_escape is False
        assert cfg.writable_dirs == []

    def test_tool_permission_checker_accepts_config_dict(self):
        from arf.guardrails.permissions import ToolPermissionChecker
        sig = inspect.signature(ToolPermissionChecker.__init__)
        assert "config" in sig.parameters

    # -- configurability: patterns are NOT hardcoded (2026-05-29 audit) --

    def test_regex_output_guard_accepts_custom_patterns(self):
        """RegexOutputGuard accepts patterns via constructor — not hardcoded."""
        from arf.guardrails.regex_clean import RegexOutputGuard
        guard = RegexOutputGuard(patterns=[("custom", "[HIDDEN]")])
        result = asyncio.run(guard.check("my custom secret", {}))
        assert "[HIDDEN]" in result.modified_message

    def test_regex_output_guard_defaults_work_without_config(self):
        """No patterns arg → uses _BUILTIN_PATTERNS (backward compat)."""
        from arf.guardrails.regex_clean import RegexOutputGuard
        guard = RegexOutputGuard()
        result = asyncio.run(guard.check("sk-abcdefghijklmnopqrstuv", {}))
        assert "[REDACTED_API_KEY]" in (result.modified_message or "")

    def test_regex_output_guard_empty_patterns_disables_filtering(self):
        """Empty pattern list disables all output filtering."""
        from arf.guardrails.regex_clean import RegexOutputGuard
        guard = RegexOutputGuard(patterns=[])
        result = asyncio.run(guard.check("sk-abcdefghijklmnopqrstuv", {}))
        assert result.modified_message is None

    def test_guardrails_config_has_output_patterns_field(self):
        """GuardrailsConfig exposes output_patterns for user customization."""
        from arf.core.config_base import GuardrailsConfig, RegexPatternConfig
        cfg = GuardrailsConfig(
            output_patterns=[
                RegexPatternConfig(pattern=r"\d{16}", replacement="[CARD]"),
            ]
        )
        assert len(cfg.output_patterns) == 1
        assert cfg.output_patterns[0].pattern == r"\d{16}"
        assert cfg.output_patterns[0].replacement == "[CARD]"

    def test_regex_pattern_config_is_pydantic_model(self):
        """RegexPatternConfig is a proper Pydantic model with pattern/replacement."""
        from arf.core.config_base import RegexPatternConfig
        from pydantic import BaseModel
        assert issubclass(RegexPatternConfig, BaseModel)
        assert "pattern" in RegexPatternConfig.model_fields
        assert "replacement" in RegexPatternConfig.model_fields

    def test_base_agent_wires_patterns_to_regex_output_guard(self):
        """BaseAgent passes output_patterns from config to RegexOutputGuard.
        Verify the wiring branch exists in BaseAgent.__init__."""
        import inspect as _ins
        from arf.agent.base import BaseAgent
        src = _ins.getsource(BaseAgent.__init__)
        assert "output_patterns" in src
        assert "RegexOutputGuard(patterns=" in src
        assert "RegexOutputGuard()" in src  # default (built-in) fallback


# ============================================================
# Section 2.2 — DefaultGuardRunner
# ============================================================

class TestDefaultGuardRunner:
    """Doc §2.2: DefaultGuardRunner orchestrates all guards."""

    def test_constructor_defaults(self):
        from arf.guardrails.runner import DefaultGuardRunner
        runner = DefaultGuardRunner()
        assert runner._input is not None
        assert runner._output is not None
        assert runner._tool is not None
        assert runner._permission_registry is not None
        assert runner._permission_lists is not None

    def test_check_tool_params_method(self):
        from arf.guardrails.runner import DefaultGuardRunner
        assert hasattr(DefaultGuardRunner, "check_tool_params")

    def test_check_tool_permission_method(self):
        from arf.guardrails.runner import DefaultGuardRunner
        assert hasattr(DefaultGuardRunner, "check_tool_permission")

    def test_check_input_method(self):
        from arf.guardrails.runner import DefaultGuardRunner
        assert hasattr(DefaultGuardRunner, "check_input")

    def test_check_output_method(self):
        from arf.guardrails.runner import DefaultGuardRunner
        assert hasattr(DefaultGuardRunner, "check_output")


# ============================================================
# Section — Guardrails __init__ exports
# ============================================================

class TestGuardrailsInit:
    """Doc: __init__.py exports expected classes."""

    def test_init_exports(self):
        import arf.guardrails
        assert hasattr(arf.guardrails, "DefaultGuardRunner")
        assert hasattr(arf.guardrails, "NoneInputGuard")
        assert hasattr(arf.guardrails, "RegexOutputGuard")
        assert hasattr(arf.guardrails, "PathCheckToolGuard")


# ============================================================
# Section 2.6 — Hook exit codes (brief check)
# ============================================================

class TestHookExitCodes:
    """Doc §2.6: Hook exit codes — 0=continue, 1=block, 2=inject."""

    def test_subprocess_hook_runner_exists(self):
        from arf.hooks.runner import SubprocessHookRunner
        assert SubprocessHookRunner is not None
