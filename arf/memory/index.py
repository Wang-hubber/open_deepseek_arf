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
    # Task memory — read/write
    # ------------------------------------------------------------------

    def load_tasks(self) -> str:
        """Read tasks.md. Returns '' if disabled or absent.
        Group file is supplementary — personal file is primary for reading.
        """
        if not self._cfg.task_memory.enabled:
            return ""
        f = self._dir / "tasks.md"
        if f.exists():
            return f.read_text(encoding="utf-8")
        return ""

    def save_tasks(self, content: str) -> None:
        """Overwrite tasks.md."""
        self._truncate_check("tasks.md", content, self._cfg.task_memory.max_size_kb)
        (self._dir / "tasks.md").write_text(content, encoding="utf-8")

    def save_group_tasks(self, content: str) -> None:
        """Overwrite group-level tasks.md. No-op if group dir not configured."""
        if not self._group_dir:
            return
        self._group_dir.mkdir(parents=True, exist_ok=True)
        self._truncate_check("group tasks.md", content, self._cfg.task_memory.max_size_kb)
        (self._group_dir / "tasks.md").write_text(content, encoding="utf-8")
        logger.info("Group task memory updated (%d chars)", len(content))

    def build_task_summary(self) -> str:
        """Build a compact summary from tasks.md for system prompt injection.

        Parses <!-- TASK {category} | agent: {agent} --> comments and
        extracts the highest-frequency lesson per category. Pure code
        parsing — no LLM call.
        """
        content = self.load_tasks()
        if not content.strip():
            return ""

        import re
        # Match: <!-- TASK category | agent: name -->
        task_header_re = re.compile(r'<!-- TASK (\S+) \| agent: (\S+) -->')
        # Match: ### description line
        desc_re = re.compile(r'### (.+)')
        # Match: - lesson text (optionally with count suffix (×N))
        lesson_re = re.compile(r'- (.+?)(?: \(×(\d+)\))?\s*$')

        categories: dict[str, dict] = {}  # category -> {description, top_lesson, count}

        lines = content.split('\n')
        current_category = None
        current_description = ""
        current_lessons: list[tuple[str, int]] = []

        def flush_category():
            if current_category and current_lessons:
                best = max(current_lessons, key=lambda x: x[1])
                categories[current_category] = {
                    "description": current_description,
                    "lesson": best[0],
                    "count": best[1],
                }

        for line in lines:
            m = task_header_re.match(line.strip())
            if m:
                flush_category()
                current_category = m.group(1)
                current_description = ""
                current_lessons = []
                continue

            if current_category and not current_description:
                dm = desc_re.match(line.strip())
                if dm:
                    current_description = dm.group(1)

            if current_category:
                lm = lesson_re.match(line.strip())
                if lm:
                    lesson_text = lm.group(1).strip()
                    cnt_str = lm.group(2)
                    cnt = int(cnt_str) if cnt_str else 1
                    current_lessons.append((lesson_text, cnt))

        flush_category()

        if not categories:
            return ""

        limit = self._cfg.task_memory.summary_limit
        lines_out = ["## Task Memory (recent)", ""]
        for i, (cat, info) in enumerate(categories.items()):
            if i >= limit:
                break
            desc = info["description"][:60]
            lesson = info["lesson"][:80]
            cnt = info["count"]
            lines_out.append(
                f"- **{cat}**: {desc} — 避坑: {lesson} (×{cnt})"
            )

        lines_out.append("")
        lines_out.append("Use `kernel__search_task_memory` to search full archive.")
        return "\n".join(lines_out)

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
        if self._cfg.task_memory.enabled:
            content = self._format_task_memory()
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

    def _format_task_memory(self) -> str:
        return self.build_task_summary()

    def _truncate_check(self, filename: str, content: str, max_kb: int) -> None:
        size_kb = len(content.encode("utf-8")) / 1024
        if size_kb > max_kb:
            logger.warning(
                "%s is %.1fKB > max %dKB — will be truncated on next load",
                filename, size_kb, max_kb)
