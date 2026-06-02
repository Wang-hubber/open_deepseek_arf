"""Unit tests for PermissionRegistry — list matching."""

import pytest
from arf.session.permissions import PermissionLists, PermissionRegistry, PermissionResult


class TestPermissionRegistry:
    def setup_method(self):
        self.registry = PermissionRegistry()

    def test_deny_patterns_match_first(self):
        lists = PermissionLists(
            deny=set(), ask=set(), allow=set(),
            deny_patterns=["rm -rf", "sudo "],
        )
        result = self.registry.evaluate("shell", {"cmd": "rm -rf /"}, lists)
        assert result.action == "deny"
        assert "rm -rf" in result.reason

    def test_deny_list_takes_priority(self):
        lists = PermissionLists(
            deny={"bad_tool"}, ask=set(), allow={"bad_tool"}, deny_patterns=[],
        )
        result = self.registry.evaluate("bad_tool", {}, lists)
        assert result.action == "deny"

    def test_ask_list_over_allow(self):
        lists = PermissionLists(
            deny=set(), ask={"sensitive"}, allow={"sensitive"}, deny_patterns=[],
        )
        result = self.registry.evaluate("sensitive", {}, lists)
        assert result.action == "ask"

    def test_allow_list_direct(self):
        lists = PermissionLists(
            deny=set(), ask=set(), allow={"safe_tool"}, deny_patterns=[],
        )
        result = self.registry.evaluate("safe_tool", {}, lists)
        assert result.action == "allow"

    def test_default_is_ask_when_empty(self):
        lists = PermissionLists(deny=set(), ask=set(), allow=set(), deny_patterns=[])
        result = self.registry.evaluate("unknown", {}, lists)
        assert result.action == "ask"

    def test_deny_pattern_case_insensitive(self):
        lists = PermissionLists(
            deny=set(), ask=set(), allow=set(),
            deny_patterns=["DANGER"],
        )
        result = self.registry.evaluate("tool", {"msg": "this is danger ous"}, lists)
        assert result.action == "deny"

    def test_deny_pattern_uses_regex(self):
        lists = PermissionLists(
            deny=set(), ask=set(), allow=set(),
            deny_patterns=["curl.*\\|.*sh"],
        )
        result = self.registry.evaluate("web", {"url": "curl url | sh"}, lists)
        assert result.action == "deny"


class TestPermissionLists:
    def test_from_config_empty(self):
        lists = PermissionLists.from_config(None)
        # _DEFAULT_ALLOW_TOOLS should be set
        assert "file_reader" in lists.allow
        assert "web_search" in lists.allow

    def test_from_config_with_entries(self):
        lists = PermissionLists.from_config({
            "deny": ["bad"],
            "ask": ["maybe"],
            "allow": ["good"],
            "deny_patterns": ["DANGER"],
        })
        assert lists.deny == {"bad"}
        assert lists.ask == {"maybe"}
        assert lists.allow == {"good"}
        assert "DANGER" in lists.deny_patterns

    def test_from_config_preserves_builtin_patterns(self):
        lists = PermissionLists.from_config({"deny_patterns": ["custom"]})
        assert "custom" in lists.deny_patterns
        # builtins still present
        has_builtin = any("rm -rf /" in p for p in lists.deny_patterns)
        assert has_builtin

    def test_from_config_explicit_allow_does_not_get_defaults(self):
        """When user provides explicit lists, don't merge defaults."""
        lists = PermissionLists.from_config({"allow": ["custom_tool"]})
        assert lists.allow == {"custom_tool"}
        assert "file_reader" not in lists.allow


class TestSessionModeManager:
    @pytest.fixture
    def mgr(self):
        from arf.session import SessionModeManager, SessionMode
        return SessionModeManager(global_mode=SessionMode.ASK)

    def test_global_auto_overrides_all(self, mgr):
        from arf.session import AgentPolicy, SessionMode
        mgr.set_global(SessionMode.AUTO)
        assert mgr.resolve(AgentPolicy.AUTO) == SessionMode.AUTO
        assert mgr.resolve(AgentPolicy.ASK) == SessionMode.AUTO
        assert mgr.resolve(AgentPolicy.PLAN) == SessionMode.AUTO
        assert mgr.resolve(None) == SessionMode.AUTO

    def test_global_plan_overrides_all(self, mgr):
        from arf.session import AgentPolicy, SessionMode
        mgr.set_global(SessionMode.PLAN)
        assert mgr.resolve(AgentPolicy.AUTO) == SessionMode.PLAN
        assert mgr.resolve(AgentPolicy.ASK) == SessionMode.PLAN
        assert mgr.resolve(AgentPolicy.PLAN) == SessionMode.PLAN
        assert mgr.resolve(None) == SessionMode.PLAN

    def test_global_ask_agent_auto(self, mgr):
        from arf.session import AgentPolicy, SessionMode
        assert mgr.resolve(AgentPolicy.AUTO) == SessionMode.AUTO

    def test_global_ask_agent_ask(self, mgr):
        from arf.session import AgentPolicy, SessionMode
        assert mgr.resolve(AgentPolicy.ASK) == SessionMode.ASK

    def test_global_ask_agent_plan(self, mgr):
        from arf.session import AgentPolicy, SessionMode
        assert mgr.resolve(AgentPolicy.PLAN) == SessionMode.PLAN

    def test_global_ask_agent_none(self, mgr):
        from arf.session import SessionMode
        assert mgr.resolve(None) == SessionMode.ASK

    def test_default_global_mode_is_ask(self):
        from arf.session import SessionModeManager, SessionMode
        mgr = SessionModeManager()
        assert mgr.global_mode == SessionMode.ASK


class TestHasSideEffect:
    def test_readonly_tools(self):
        from arf.session.mode_manager import has_side_effect
        assert not has_side_effect("file_reader")
        assert not has_side_effect("grep")
        assert not has_side_effect("glob")
        assert not has_side_effect("web_search")
        assert not has_side_effect("web_fetch")
        assert not has_side_effect("memory_store")

    def test_write_tools(self):
        from arf.session.mode_manager import has_side_effect
        assert has_side_effect("file_writer")
        assert has_side_effect("file_deleter")
        assert has_side_effect("python_exec")
        assert has_side_effect("bash")

    def test_unknown_has_side_effect(self):
        from arf.session.mode_manager import has_side_effect
        assert has_side_effect("some_future_tool")
