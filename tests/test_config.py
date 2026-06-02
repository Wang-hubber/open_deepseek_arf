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


class TestMcpServerConfig:
    def test_defaults(self):
        from arf.core.config_base import McpServerConfig
        cfg = McpServerConfig(name="test")
        assert cfg.name == "test"
        assert cfg.transport == "sse"
        assert cfg.url == ""
        assert cfg.command == ""
        assert cfg.args == []
        assert cfg.api_key_env == ""
        assert cfg.timeout == "30s"

    def test_full_config(self):
        from arf.core.config_base import McpServerConfig
        cfg = McpServerConfig(
            name="ci",
            transport="http",
            url="http://localhost:9001",
            api_key_env="MCP_KEY",
            timeout="60s",
        )
        assert cfg.transport == "http"
        assert cfg.url == "http://localhost:9001"

    def test_agent_config_parses_mcp_servers(self):
        import yaml
        from arf.agent.config import AgentConfig
        raw = yaml.safe_load("""
name: test
mcp_servers:
  - name: search
    transport: sse
    url: http://localhost:9000/sse
  - name: ci
    transport: http
    url: http://localhost:9001
    api_key_env: MCP_CI_KEY
""")
        cfg = AgentConfig(**raw)
        assert len(cfg.mcp_servers) == 2
        assert cfg.mcp_servers[0].name == "search"
        assert cfg.mcp_servers[0].transport == "sse"
        assert cfg.mcp_servers[1].name == "ci"
        assert cfg.mcp_servers[1].api_key_env == "MCP_CI_KEY"

    def test_default_mcp_servers_empty(self):
        from arf.agent.config import AgentConfig
        cfg = AgentConfig(name="test")
        assert cfg.mcp_servers == []
