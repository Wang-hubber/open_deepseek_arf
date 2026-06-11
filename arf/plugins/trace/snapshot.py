"""EnvSnapshotBuilder — scan local config files, produce hash-addressed XML snapshot."""
import hashlib
import time
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring


class EnvSnapshotBuilder:
    """Scan plugins_root for plugin/tool/skill configs, build XML snapshot.

    Produces a deterministic XML document whose SHA256 hash acts as a
    content-addressed identifier for this exact configuration state.

    Usage:
        builder = EnvSnapshotBuilder("./arf/plugins", ["./agent.yaml"])
        xml_str, hash_val = builder.build()
        # xml_str → save to snapshots/{hash_val}.xml
    """

    def __init__(self, plugins_root: str,
                 extra_files: list[str] | None = None):
        self._plugins_root = Path(plugins_root)
        self._extra_files = [Path(f) for f in (extra_files or [])]

    def build(self) -> tuple[str, str]:
        """Build XML snapshot. Returns (xml_string, sha256_12char_hash)."""
        root = Element("snapshot")

        # -- Agent config (extra files) --
        agent_el = SubElement(root, "agent")
        for f in self._extra_files:
            self._append_file(agent_el, f, "config")

        # -- Plugins --
        plugins_el = SubElement(root, "plugins", {
            "root": str(self._plugins_root),
        })

        if self._plugins_root.exists():
            for plugin_dir in sorted(self._plugins_root.iterdir()):
                if not plugin_dir.is_dir():
                    continue
                self._scan_plugin(plugins_el, plugin_dir)

        # Hash from content only (timestamp is not part of the content address)
        xml_str = tostring(root, encoding="unicode")
        hash_val = hashlib.sha256(xml_str.encode("utf-8")).hexdigest()[:12]

        # Add runtime metadata after hashing so identical configs produce
        # the same hash regardless of when build() is called.
        root.set("created_at", str(time.time()))
        root.set("hash", hash_val)
        xml_str = tostring(root, encoding="unicode")

        return xml_str, hash_val

    # -- Internal scanning -----------------------------------------------

    def _scan_plugin(self, parent: Element, plugin_dir: Path) -> None:
        name = plugin_dir.name
        plugin_el = SubElement(parent, "plugin", {"name": name})

        # plugin.yaml
        yaml_file = plugin_dir / "plugin.yaml"
        if yaml_file.exists():
            self._append_file(plugin_el, yaml_file, "config")

        # tools/
        tools_dir = plugin_dir / "tools"
        if tools_dir.exists() and tools_dir.is_dir():
            tools_el = None
            for tool_dir in sorted(tools_dir.iterdir()):
                if not tool_dir.is_dir():
                    continue
                if tools_el is None:
                    tools_el = SubElement(plugin_el, "tools")
                self._scan_tool(tools_el, tool_dir)

        # skills/
        skills_dir = plugin_dir / "skills"
        if skills_dir.exists() and skills_dir.is_dir():
            skills_el = SubElement(plugin_el, "skills")
            for skill_file in sorted(skills_dir.iterdir()):
                if skill_file.suffix in (".yaml", ".yml"):
                    self._append_file(
                        skills_el, skill_file, "skill",
                        extra={"name": skill_file.stem},
                    )

    def _scan_tool(self, parent: Element, tool_dir: Path) -> None:
        tool_el = SubElement(parent, "tool", {"name": tool_dir.name})

        tool_yaml = tool_dir / "tool.yaml"
        if tool_yaml.exists():
            self._append_file(tool_el, tool_yaml, "definition")

        func_py = tool_dir / "function.py"
        if func_py.exists():
            self._append_file(tool_el, func_py, "implementation")

    @staticmethod
    def _append_file(parent: Element, path: Path, tag: str,
                     extra: dict | None = None) -> None:
        """Read file content into a child element."""
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return

        attrs = {"src": str(path)}
        if extra:
            attrs.update(extra)

        el = SubElement(parent, tag, attrs)
        el.text = content
