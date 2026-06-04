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
