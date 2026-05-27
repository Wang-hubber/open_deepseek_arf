from arf.core.config_base import HandoverContextConfig, HandoverRuleConfig


class TestHandoverContextConfig:
    def test_defaults(self):
        ctx = HandoverContextConfig()
        assert ctx.raw_turns == 5
        assert ctx.task_summary is True

    def test_raw_turns_negative_one(self):
        ctx = HandoverContextConfig(raw_turns=-1)
        assert ctx.raw_turns == -1

    def test_raw_turns_zero(self):
        ctx = HandoverContextConfig(raw_turns=0)
        assert ctx.raw_turns == 0


class TestHandoverRuleConfig:
    def test_with_custom_context(self):
        rule = HandoverRuleConfig(
            from_agent="arf_assistant",
            to_agent="sys_agent",
            trigger="create tool",
            context={"raw_turns": 10, "task_summary": False},
        )
        assert rule.context.raw_turns == 10
        assert rule.context.task_summary is False

    def test_default_context(self):
        rule = HandoverRuleConfig(
            from_agent="a", to_agent="b", trigger="test"
        )
        assert rule.context.raw_turns == 5
        assert rule.context.task_summary is True
