# arf/memory/index.py
"""MemoryIndex — load, save, and format three memory layers for injection."""
import logging
from pathlib import Path

logger = logging.getLogger("arf.memory.index")


class MemoryIndex:
    """Central manager for project, user, and secrets memory.

    Reads from {data_dir}/memory/{project,user}.md and secrets.enc.
    Formats enabled layers as system messages for ControlPlane injection.
    """

    def __init__(self, data_dir: str, config,  # MemoryConfig
                 secrets_store=None,  # SecretsStore | None
                 group_memory_dir: str = "") -> None:
        self._dir = Path(data_dir) / "memory"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._group_dir = Path(group_memory_dir) if group_memory_dir else None
        self._cfg = config
        self._secrets = secrets_store
        if self._cfg.secrets.enabled and self._secrets is None:
            raise RuntimeError(
                f"Secrets memory is enabled but no SecretsStore provided. "
                f"Set {self._cfg.secrets.master_key_env} or disable secrets "
                f"via config: MemoryConfig(secrets={{'enabled': False}})."
            )

    # ------------------------------------------------------------------
    # Public — read
    # ------------------------------------------------------------------

    def load_project(self) -> str:
        """Read project.md. Returns '' if disabled or absent.

        When group project.md exists it is the single source of truth
        (shared across team). Individual project.md only loads as a
        fallback when no group file exists.
        """
        if not self._cfg.project.enabled:
            return ""
        if self._group_dir:
            gf = self._group_dir / "project.md"
            if gf.exists():
                return f"<!-- shared project memory -->\n{gf.read_text(encoding='utf-8')}"
        f = self._dir / "project.md"
        if f.exists():
            return f.read_text(encoding="utf-8")
        return ""

    def load_user(self) -> str:
        """Read user.md. Returns '' if disabled or absent.

        When group user.md exists it is the single source of truth.
        Individual user.md only loads as a fallback.
        """
        if not self._cfg.user.enabled:
            return ""
        if self._group_dir:
            gf = self._group_dir / "user.md"
            if gf.exists():
                return f"<!-- shared user memory -->\n{gf.read_text(encoding='utf-8')}"
        f = self._dir / "user.md"
        if f.exists():
            return f.read_text(encoding="utf-8")
        return ""

    def list_secrets(self) -> list[str]:
        """List secret names. Returns [] if disabled or no store."""
        if not self._cfg.secrets.enabled or self._secrets is None:
            return []
        return self._secrets.list_names()

    # ------------------------------------------------------------------
    # Public — write
    # ------------------------------------------------------------------

    def save_project(self, content: str) -> None:
        """Overwrite project.md."""
        self._truncate_check("project.md", content, self._cfg.project.max_size_kb)
        (self._dir / "project.md").write_text(content, encoding="utf-8")

    def save_user(self, content: str) -> None:
        """Overwrite user.md."""
        self._truncate_check("user.md", content, self._cfg.user.max_size_kb)
        (self._dir / "user.md").write_text(content, encoding="utf-8")

    def save_group_project(self, content: str) -> None:
        """Overwrite group-level project.md (shared across team). No-op if group dir not configured."""
        if not self._group_dir:
            return
        self._group_dir.mkdir(parents=True, exist_ok=True)
        self._truncate_check("group project.md", content, self._cfg.project.max_size_kb)
        (self._group_dir / "project.md").write_text(content, encoding="utf-8")
        logger.info("Group project memory updated (%d chars)", len(content))

    def save_group_user(self, content: str) -> None:
        """Overwrite group-level user.md (shared across team). No-op if group dir not configured."""
        if not self._group_dir:
            return
        self._group_dir.mkdir(parents=True, exist_ok=True)
        self._truncate_check("group user.md", content, self._cfg.user.max_size_kb)
        (self._group_dir / "user.md").write_text(content, encoding="utf-8")
        logger.info("Group user memory updated (%d chars)", len(content))

    # ------------------------------------------------------------------
    # Public — injection
    # ------------------------------------------------------------------

    def build_injected_messages(self) -> list[dict]:
        """Return system messages for enabled memory layers."""
        msgs = []
        if self._cfg.project.enabled:
            content = self._format_project()
            if content:
                msgs.append({"role": "system", "content": content})
        if self._cfg.user.enabled:
            content = self._format_user()
            if content:
                msgs.append({"role": "system", "content": content})
        if self._cfg.secrets.enabled and self._secrets is not None:
            content = self._format_secrets()
            if content:
                msgs.append({"role": "system", "content": content})
        return msgs

    def has_project_file(self) -> bool:
        return (self._dir / "project.md").exists()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _format_project(self) -> str:
        text = self.load_project().strip()
        return f"## Project Memory\n\n{text}" if text else ""

    def _format_user(self) -> str:
        text = self.load_user().strip()
        return f"## User Memory\n\n{text}" if text else ""

    def _format_secrets(self) -> str:
        names = self.list_secrets()
        if not names:
            return ""
        lines = "## Available Secrets\n"
        lines += "\n".join(f"- {n}" for n in names)
        return lines

    def _truncate_check(self, filename: str, content: str, max_kb: int) -> None:
        size_kb = len(content.encode("utf-8")) / 1024
        if size_kb > max_kb:
            logger.warning(
                "%s is %.1fKB > max %dKB — will be truncated on next load",
                filename, size_kb, max_kb)
