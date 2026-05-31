"""AppContext — app declares its root, framework derives standard paths from it."""
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class AppContext:
    """App注册自己的根目录，框架以此推导所有标准子路径。

    使用方式:
        ctx = AppContext(root=Path(__file__).parent)
        agent = create_agent(config=cfg, app_context=ctx)
    """

    root: Path
    workspace: str = "workspace"

    # -- derived paths --
    @property
    def config_path(self) -> Path:
        return self.root / "agent.yaml"

    @property
    def tools_dir(self) -> Path:
        return self.root / "tools"

    @property
    def skills_dir(self) -> Path:
        return self.root / "skills"

    @property
    def models_dir(self) -> Path:
        return self.root / "models"

    @property
    def hooks_dir(self) -> Path:
        return self.root / "hooks"

    @property
    def workspace_dir(self) -> Path:
        return self.root / self.workspace

    @property
    def state_dir(self) -> Path:
        return self.workspace_dir / "state"

    @property
    def trace_dir(self) -> Path:
        return self.workspace_dir / "traces"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"
