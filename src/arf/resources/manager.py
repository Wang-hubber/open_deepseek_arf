"""Resource manager -- dual-source loading, registration, indexing, and permission checks."""

import importlib.util
from pathlib import Path

import yaml


class ResourceConflictError(Exception):
    """Raised when a user resource conflicts with a system resource."""


class ResourceRegistry:
    """Scans convention directories and maintains a unified resource index
    with source tracking and read-only enforcement."""

    def __init__(self):
        self._items: dict[str, dict[str, dict]] = {
            "models": {},
            "tools": {},
            "skills": {},
        }

    # ---- unified load -----------------------------------------------

    def load(self, system_dir: str, workspace_dir: str | None = None) -> None:
        """Load system resources first, then user resources.
        Conflicts (user defining same name as system) raise ResourceConflictError.
        """
        self._load_dir(system_dir, source="system", readonly=True)
        if workspace_dir:
            self._load_dir(workspace_dir, source="user", readonly=False)

    def _load_dir(self, base_dir: str, source: str, readonly: bool) -> None:
        root = Path(base_dir)
        self._scan_models(root / "models", source, readonly)
        self._scan_tools(root / "tools", source, readonly)
        self._scan_skills(root / "skills", source, readonly)

    # ---- scanners ----------------------------------------------------

    def _scan_models(self, models_dir: Path, source: str, readonly: bool):
        if not models_dir.exists():
            return
        for sub in models_dir.iterdir():
            if not sub.is_dir():
                continue
            cfg_file = sub / "config.yaml"
            cfg_default = sub / "config_default.yaml"
            if not cfg_file.exists() and not cfg_default.exists():
                continue
            cfg = self._read_yaml(cfg_file) if cfg_file.exists() else {}
            default = self._read_yaml(cfg_default) if cfg_default.exists() else {}
            name = cfg.get("name") or default.get("name", sub.name)
            configured = cfg_file.exists() and source != "system"
            item = self._build_model_item(name, cfg, default, sub, source, readonly, configured)
            self._register("models", name, item)

    def _scan_tools(self, tools_dir: Path, source: str, readonly: bool):
        if not tools_dir.exists():
            return
        for sub in tools_dir.iterdir():
            if not sub.is_dir():
                continue
            cfg_file = sub / "tool.yaml"
            cfg_default = sub / "config_default.yaml"
            if not cfg_file.exists() and not cfg_default.exists():
                continue
            cfg = self._read_yaml(cfg_file) if cfg_file.exists() else {}
            default = self._read_yaml(cfg_default) if cfg_default.exists() else {}
            name = cfg.get("name") or default.get("name", sub.name)
            configured = cfg_file.exists() and source != "system"
            schema = self._normalize_schema(cfg, name)
            item = {
                "type": "tool",
                "name": name,
                "description": cfg.get("description") or default.get("description", ""),
                "json_schema": schema,
                "path": str(sub),
                "source": source,
                "readonly": readonly,
                "function": self._load_tool_function(sub) if cfg_file.exists() else None,
                "depends_on": default.get("depends_on", []),
                "required": default.get("required", False),
                "configured": configured,
            }
            self._register("tools", name, item)

    def _scan_skills(self, skills_dir: Path, source: str, readonly: bool):
        if not skills_dir.exists():
            return
        for sub in skills_dir.iterdir():
            if not sub.is_dir():
                continue
            cfg_file = sub / "skill.yaml"
            cfg_default = sub / "config_default.yaml"
            if not cfg_file.exists() and not cfg_default.exists():
                continue
            cfg = self._read_yaml(cfg_file) if cfg_file.exists() else {}
            default = self._read_yaml(cfg_default) if cfg_default.exists() else {}
            name = cfg.get("name") or default.get("name", sub.name)
            configured = cfg_file.exists() and source != "system"
            item = {
                "type": "skill",
                "name": name,
                "description": cfg.get("description") or default.get("description", ""),
                "prompt_template": cfg.get("prompt_template", ""),
                "tools": cfg.get("tools", []),
                "sub_skills": cfg.get("sub_skills", []),
                "parameters": cfg.get("parameters", {}),
                "path": str(sub),
                "source": source,
                "readonly": readonly,
                "depends_on": default.get("depends_on", []),
                "required": default.get("required", False),
                "configured": configured,
            }
            self._register("skills", name, item)

    # ---- item builders -------------------------------------------------

    @staticmethod
    def _build_model_item(name: str, cfg: dict, default: dict, sub: Path,
                          source: str, readonly: bool, configured: bool) -> dict:
        return {
            "type": "model",
            "name": name,
            "description": cfg.get("description") or default.get("description", ""),
            "model_type": cfg.get("model_type") or default.get("model_type", "deep_thinking"),
            "config": cfg.get("config", {}),
            "config_template": default.get("config_template", {}),
            "config_page": default.get("config_page", ""),
            "path": str(sub),
            "source": source,
            "readonly": readonly,
            "depends_on": default.get("depends_on", []),
            "required": default.get("required", False),
            "configured": configured,
        }

    # ---- registration with conflict check ----------------------------

    def _register(self, rtype: str, name: str, item: dict) -> None:
        existing = self._items[rtype].get(name)
        if rtype == "models" and existing and existing.get("source") == "system" and item.get("source") == "user":
            # User model config merges on top of system defaults
            merged = dict(existing)
            merged["config"] = {**existing.get("config", {}), **item.get("config", {})}
            merged["path"] = item["path"]
            merged["source"] = "user"
            merged["readonly"] = False
            merged["configured"] = True
            self._items[rtype][name] = merged
            return
        # For tools/skills: user version overrides system (clone workflow)
        # But preserve system config_default.yaml metadata (depends_on, required, config_template)
        if existing and existing.get("source") == "system" and item.get("source") == "user":
            for key in ("depends_on", "required", "config_template", "config_page"):
                if key in existing and key not in item:
                    item[key] = existing[key]
            item["configured"] = True
        # For re-configuring user resources, preserve metadata from previous registration
        if existing and existing.get("source") == "user" and item.get("source") == "user":
            for key in ("depends_on", "required", "config_template", "config_page"):
                if key in existing and key not in item:
                    item[key] = existing[key]
        self._items[rtype][name] = item

    # ---- tool function loading ---------------------------------------

    @staticmethod
    def _normalize_schema(cfg: dict, tool_name: str) -> dict:
        """Normalize tool parameter definitions to JSON Schema (OpenAI/DeepSeek format).

        Accepts two input formats:

        1. Canonical JSON Schema (object form):
           parameters:
             type: object
             properties:
               foo: {type: string, description: "..."}
             required: [foo]

        2. Simplified list form:
           parameters:
             - name: foo
               type: string
               required: true
               description: "..."

        Both are converted to the canonical JSON Schema object form.
        """
        params = cfg.get("parameters") or cfg.get("json_schema") or {}

        # Already a dict with "type" or "properties" -> assume canonical
        if isinstance(params, dict):
            if "type" in params or "properties" in params:
                return params
            if not params:
                return {"type": "object", "properties": {}}

        # List form: convert to JSON Schema
        if isinstance(params, list):
            properties: dict = {}
            required: list = []
            for p in params:
                if not isinstance(p, dict) or "name" not in p:
                    continue
                pname = p["name"]
                prop: dict = {"type": p.get("type", "string")}
                if "description" in p:
                    prop["description"] = p["description"]
                if "enum" in p:
                    prop["enum"] = p["enum"]
                if "default" in p:
                    prop["default"] = p["default"]
                properties[pname] = prop
                if p.get("required"):
                    required.append(pname)
            return {
                "type": "object",
                "properties": properties,
                "required": required,
            }

        # Fallback: return as-is (will likely fail at API level, but logged)
        import logging
        logging.getLogger("arf").warning(
            "Tool '%s' has unrecognized parameters format (type=%s), "
            "this may cause API errors",
            tool_name, type(params).__name__,
        )
        return params

    @staticmethod
    def _load_tool_function(tool_dir: Path):
        func_file = tool_dir / "function.py"
        if not func_file.exists():
            return None
        try:
            spec = importlib.util.spec_from_file_location(
                f"tool_{tool_dir.name}", func_file
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return getattr(mod, "execute", None)
        except Exception as e:
            import logging
            logging.getLogger("arf").warning(
                "Failed to load tool function '%s': %s: %s",
                tool_dir.name, type(e).__name__, e,
            )
            return None

    # ---- accessors ---------------------------------------------------

    def get_tool(self, name: str) -> dict | None:
        return self._items["tools"].get(name)

    def get(self, resource_type: str, name: str) -> dict | None:
        return self._items.get(resource_type, {}).get(name)

    def is_readonly(self, resource_type: str, name: str) -> bool | None:
        """Return whether a resource is read-only, or None if not found."""
        item = self.get(resource_type, name)
        if item is None:
            return None
        return item.get("readonly", False)

    def count(self, resource_type: str) -> int:
        return len(self._items.get(resource_type, {}))

    def list_unconfigured(self, required_only: bool = False) -> list[dict]:
        """Return resources that are not yet configured by the user.
        If required_only is True, only return required resources.
        """
        result: list[dict] = []
        for rtype in ("models", "tools", "skills"):
            for r in self._items[rtype].values():
                if r.get("configured", True):
                    continue
                if required_only and not r.get("required", False):
                    continue
                result.append({
                    "name": r["name"],
                    "type": rtype.rstrip("s"),  # "models" -> "model"
                    "description": r.get("description", ""),
                    "required": r.get("required", False),
                    "depends_on": r.get("depends_on", []),
                    "config_template": r.get("config_template", {}),
                    "model_type": r.get("model_type"),
                })
        # Compatibility: if legacy "default" model is configured,
        # it satisfies the "deep_thinking" required slot
        default_model = self._items["models"].get("default")
        if default_model and default_model.get("configured"):
            result = [u for u in result
                      if not (u["type"] == "model" and u["name"] == "deep_thinking")]
        return result

    def check_deps(self, resource_type: str, name: str) -> dict:
        """Check if all dependencies of a resource are configured.
        Returns {"ok": bool, "missing": [...]} where missing lists
        unconfigured dependencies.
        """
        item = self._items.get(resource_type, {}).get(name)
        if not item:
            return {"ok": False, "missing": [], "error": f"Resource '{name}' not found"}
        missing: list[dict] = []
        for dep in item.get("depends_on", []):
            dep_type = dep.get("type", "") + "s"  # "model" -> "models"
            dep_name = dep.get("name", "")
            dep_item = self._items.get(dep_type, {}).get(dep_name)
            if not dep_item or not dep_item.get("configured", False):
                missing.append({
                    "type": dep.get("type", ""),
                    "name": dep_name,
                    "description": dep_item.get("description", "") if dep_item else "",
                })
        return {"ok": len(missing) == 0, "missing": missing}

    # ---- listing -----------------------------------------------------

    def list_all(self) -> dict[str, list[dict]]:
        """Return all registered resources grouped by type with shallow info."""
        result = {}
        for rtype in ("models", "tools", "skills"):
            items = []
            for r in self._items[rtype].values():
                item = {
                    "name": r["name"],
                    "description": r.get("description", ""),
                    "source": r.get("source", "user"),
                    "readonly": r.get("readonly", False),
                    "configured": r.get("configured", False),
                    "required": r.get("required", False),
                    "depends_on": r.get("depends_on", []),
                }
                if rtype == "models":
                    cfg = r.get("config", {})
                    if cfg.get("model_name"):
                        item["model_name"] = cfg["model_name"]
                    item["model_type"] = r.get("model_type")
                    item["config_template"] = r.get("config_template", {})
                    item["config_page"] = r.get("config_page", "")
                items.append(item)
            result[rtype] = items
        return result

    def list_by_source(self, source: str) -> dict[str, list[dict]]:
        """Return resources filtered by source ('system' or 'user')."""
        result = {}
        for rtype in ("models", "tools", "skills"):
            result[rtype] = [
                {
                    "name": r["name"],
                    "description": r.get("description", ""),
                    "readonly": r.get("readonly", False),
                }
                for r in self._items[rtype].values()
                if r.get("source") == source
            ]
        return result

    # ---- hot-reload --------------------------------------------------

    def reload_user(self, workspace_dir: str) -> list[str]:
        """Re-scan user workspace and update registry in-place.
        Adds new resources, updates changed ones, removes deleted ones.
        Returns a list of change descriptions like ['+tools/foo', '~skills/bar', '-tools/baz'].
        """
        changes = []
        root = Path(workspace_dir)
        for rtype in ("models", "tools", "skills"):
            changes.extend(self._reload_dir(root / rtype, rtype))
        return changes

    def _reload_dir(self, base_dir: Path, rtype: str) -> list[str]:
        current_dirs: set[str] = set()
        changes: list[str] = []

        if not base_dir.exists():
            to_remove = [n for n, r in self._items[rtype].items() if r.get("source") == "user"]
            for name in to_remove:
                del self._items[rtype][name]
                changes.append(f"-{rtype}/{name}")
            return changes

        for sub in base_dir.iterdir():
            if not sub.is_dir():
                continue
            cfg_file = sub / self._config_filename(rtype)
            if not cfg_file.exists():
                continue
            current_dirs.add(sub.name)

            cfg = self._read_yaml(cfg_file)
            name = cfg.get("name", sub.name)
            item = self._build_item(rtype, name, cfg, sub)

            existing = self._items[rtype].get(name)
            if existing is None:
                changes.append(f"+{rtype}/{name}")
            elif existing.get("source") == "user":
                changes.append(f"~{rtype}/{name}")
            else:
                continue  # skip system resources
            self._register(rtype, name, item)

        # Remove user resources that no longer exist on disk
        to_remove = [
            n for n, r in self._items[rtype].items()
            if r.get("source") == "user" and n not in current_dirs
        ]
        for name in to_remove:
            del self._items[rtype][name]
            changes.append(f"-{rtype}/{name}")

        return changes

    @staticmethod
    def _config_filename(rtype: str) -> str:
        if rtype == "tools":
            return "tool.yaml"
        if rtype == "skills":
            return "skill.yaml"
        return "config.yaml"

    def _build_item(self, rtype: str, name: str, cfg: dict, sub: Path) -> dict:
        """Build a registry item from parsed config (user resources only).
        Reads metadata from cfg (config.yaml) first, falling back to
        config_default.yaml in the same directory."""
        cfg_default = sub / "config_default.yaml"
        default = self._read_yaml(cfg_default) if cfg_default.exists() else {}
        if rtype == "models":
            return {
                "type": "model", "name": name,
                "description": cfg.get("description") or default.get("description", ""),
                "model_type": cfg.get("model_type") or default.get("model_type", "deep_thinking"),
                "config": cfg.get("config", {}),
                "config_template": cfg.get("config_template") or default.get("config_template", {}),
                "config_page": cfg.get("config_page") or default.get("config_page", ""),
                "path": str(sub), "source": "user", "readonly": False,
                "depends_on": cfg.get("depends_on") or default.get("depends_on", []),
                "required": cfg.get("required") if "required" in cfg else default.get("required", False),
                "configured": True,
            }
        if rtype == "tools":
            schema = self._normalize_schema(cfg, name)
            return {
                "type": "tool", "name": name,
                "description": cfg.get("description") or default.get("description", ""),
                "json_schema": schema,
                "path": str(sub), "source": "user", "readonly": False,
                "function": self._load_tool_function(sub),
                "depends_on": cfg.get("depends_on") or default.get("depends_on", []),
                "required": cfg.get("required") if "required" in cfg else default.get("required", False),
                "configured": True,
            }
        # skills
        return {
            "type": "skill", "name": name,
            "description": cfg.get("description") or default.get("description", ""),
            "prompt_template": cfg.get("prompt_template", ""),
            "tools": cfg.get("tools", []),
            "sub_skills": cfg.get("sub_skills", []),
            "parameters": cfg.get("parameters", {}),
            "path": str(sub), "source": "user", "readonly": False,
            "depends_on": cfg.get("depends_on") or default.get("depends_on", []),
            "required": cfg.get("required") if "required" in cfg else default.get("required", False),
            "configured": True,
        }

    # ---- helpers -----------------------------------------------------

    @staticmethod
    def _read_yaml(path: Path) -> dict:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
