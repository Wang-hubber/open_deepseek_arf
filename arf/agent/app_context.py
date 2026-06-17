"""AppContext — app declares its root, framework derives standard paths from it."""
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class AppContext:
    """App registers its root directory; framework derives all standard sub-paths.

    Usage:
        ctx = AppContext(root=Path(__file__).parent)
        agent = create_agent(config=cfg, app_context=ctx)

    All runtime data lives under ``data/``::

        data/
        ├── state/    # session state (checkpoints)
        ├── traces/   # trace events
        ├── memory/   # project.md, user.md, secrets.enc
        └── files/    # tool output / user workspace files
    """

    root: Path
    data: str = "data"

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
    def data_dir(self) -> Path:
        return self.root / self.data

    @property
    def state_dir(self) -> Path:
        """Deprecated: sessions now scoped under data/{sid}/state/."""
        return self.data_dir

    @property
    def trace_dir(self) -> Path:
        """Deprecated: sessions now scoped under data/{sid}/traces/."""
        return self.data_dir

    @property
    def memory_dir(self) -> Path:
        return self.data_dir / "memory"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def workspace_dir(self) -> Path:
        """Workspace = app root. All paths derive from here."""
        return self.root

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"
