"""Bus-actor tool nodes for the multi_agent_team example.

Issue 2 wiring:
- `PythonToolNode` reads the example's `tools/{name}/{tool.yaml,
  function.py}` layout, registers as `node_type="mcp"` with a
  `capabilities.tools[]` array, and dispatches `tool_exec` messages
  by tool name. Tasks (read_file / list_dir / write_file etc.) all
  flow through this same path — same shape for both `permission: allow`
  and `permission: ask`.
- `PermissionRequestHandlerNode` listens on `permission_request`
  broadcasts from the engine, registers pending requests in the
  in-process `ApprovalRegistry`, and waits on an `asyncio.Event`
  until the human calls `/approve/<id>` or `/reject/<id>`. It then
  sends a `permission_response` reply keyed by the request's
  `correlation_id` so the engine's `WaitEvent` matches.

Both nodes live on the standard Bus; they do not need any framework
modification beyond the `Engine::request_permission` flow that is
already wired (Phase 9 F-017).
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from arf._arf import (
    Bus,
    MessageFilter,
    NodeId,
    NodeInfo,
    ToMatch,
)

logger = logging.getLogger("multi_agent_team.tool_nodes")


# ════════════════════════════════════════════════════════════════════
# PythonToolNode — MCP-shaped dispatcher for example-style tools
# ════════════════════════════════════════════════════════════════════


class PythonToolNode:
    """In-process MCP node that hosts the example's `tools/<name>/` defs.

    Each tool directory must contain:
      - `tool.yaml` — `{name, description, parameters: {JSON Schema}}`
      - `function.py` — exporting `async def execute(**kwargs) -> dict`

    The node registers on the bus as `mcp/<namespace>` with
    `node_type="mcp"` and a `capabilities.tools: [{name, description,
    params_schema}]` array. When the engine sends a `tool_exec`
    directed at us, we import the function module (lazy + cached),
    call `execute(**arguments)`, and reply `tool_result` with
    `correlation_id` echoed.

    The node responds only to messages routed at it (`DirectedToMe`)
    — other MCP nodes that don't own this tool simply stay silent
    (matches the McpNode convention).
    """

    def __init__(
        self,
        bus: Bus,
        tools_dir: Path,
        namespace: str = "workspace",
        workspace_root: Optional[Path] = None,
    ) -> None:
        self.bus = bus
        self.tools_dir = Path(tools_dir)
        self.namespace = namespace
        self.workspace_root = (
            Path(workspace_root) if workspace_root else self.tools_dir.parent
        )
        self._node_id = NodeId(f"mcp/{namespace}")
        self._node_id_str = f"mcp/{namespace}"
        # tool_name -> {execute_coro, description, params_schema}
        self._tools: dict[str, dict[str, Any]] = {}
        # tool_name -> loaded module (to avoid re-import)
        self._modules: dict[str, Any] = {}
        self._handle: Any = None  # NodeHandle (mutable, async recv)
        self._listener: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Discover tools under `tools_dir` and connect to the bus."""
        self._discover_tools()
        info = NodeInfo(
            node_id=self._node_id_str,
            node_type="mcp",
            capabilities={
                "kind": "mcp",
                "namespace": self.namespace,
                "tools": [
                    {
                        "name": t["name"],
                        "description": t["description"],
                        "params_schema": t["params_schema"],
                    }
                    for t in self._tools.values()
                ],
            },
            online_since=0,
        )
        flt = MessageFilter(types=["tool_exec"], to_match=ToMatch.DirectedToMe)
        self._handle = await self.bus.connect(info, flt)
        self._listener = asyncio.create_task(self._loop(), name=f"tool-node:{self.namespace}")
        logger.info(
            "PythonToolNode registered (node_id=%s, tools=%s)",
            self._node_id,
            sorted(self._tools.keys()),
        )

    def stop(self) -> None:
        if self._listener and not self._listener.done():
            self._listener.cancel()

    def _discover_tools(self) -> None:
        if not self.tools_dir.is_dir():
            logger.warning("tools_dir %s missing; node has no tools", self.tools_dir)
            return
        for entry in sorted(self.tools_dir.iterdir()):
            tool_dir = entry
            if not tool_dir.is_dir():
                continue
            yaml_path = tool_dir / "tool.yaml"
            py_path = tool_dir / "function.py"
            if not yaml_path.is_file() or not py_path.is_file():
                continue
            try:
                cfg = self._load_tool_yaml(yaml_path)
            except Exception as e:
                logger.warning("skip %s: %s", yaml_path, e)
                continue
            self._tools[cfg["name"]] = cfg

    @staticmethod
    def _load_tool_yaml(path: Path) -> dict[str, Any]:
        # Lightweight YAML loader — the example's tool.yaml files are flat
        # enough that we don't need a full YAML lib. Avoids a dependency on
        # PyYAML just for this.
        try:
            import yaml  # type: ignore
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except ImportError:
            data = _parse_minimal_yaml(path.read_text(encoding="utf-8"))
        name = data.get("name")
        if not name:
            raise ValueError(f"{path}: missing top-level `name:`")
        return {
            "name": str(name),
            "description": str(data.get("description", "")),
            "params_schema": data.get("parameters") or {},
            "path": path,
        }

    def _load_module(self, name: str, tool_dir: Path) -> Any:
        if name in self._modules:
            return self._modules[name]
        py_path = tool_dir / "function.py"
        spec = importlib.util.spec_from_file_location(
            f"tools_runtime.{name}", str(py_path)
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"failed to load {py_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        if not hasattr(module, "execute"):
            raise RuntimeError(f"{py_path}: missing `execute` symbol")
        self._modules[name] = module
        return module

    async def _loop(self) -> None:
        assert self._handle is not None
        while True:
            try:
                msg = await self._handle.recv()
            except Exception as e:
                logger.info("PythonToolNode listener exiting: %s", e)
                return
            if msg.msg_type != "tool_exec":
                continue
            await self._dispatch(msg)

    async def _dispatch(self, msg) -> None:
        payload = msg.payload
        tool_name = (
            payload.get("tool_name")
            if hasattr(payload, "get")
            else None
        )
        tool = self._tools.get(tool_name or "")
        if tool is None:
            # Engine broadcast tool_exec to every MCP node; nodes that
            # don't own this tool stay silent.
            return
        cid = (
            payload.get("correlation_id")
            if hasattr(payload, "get")
            else None
        )
        arguments = (
            payload.get("arguments", {})
            if hasattr(payload, "get")
            else {}
        )
        if hasattr(arguments, "items"):
            arg_dict = dict(arguments)
        elif isinstance(arguments, dict):
            arg_dict = arguments
        else:
            arg_dict = json.loads(str(arguments))
        # Inject workspace root so tools can resolve relative paths.
        arg_dict["_workspace"] = str(self.workspace_root)

        try:
            module = self._load_module(tool_name, tool["path"].parent)
            execute = module.execute
            result = await execute(**arg_dict)
            ok = True
            content = result
            error = None
        except Exception as e:
            ok = False
            content = {"error": str(e)}
            error = str(e)
            logger.warning("tool %s failed: %s", tool_name, e)

        reply = {
            "correlation_id": cid,
            "name": tool_name,
            "ok": ok,
            "content": content,
            "error": error,
        }
        try:
            await self._handle.send(
                "tool_result",
                [msg.sender],
                reply,
            )
        except Exception as e:
            logger.error("tool_node failed to send tool_result: %s", e)


def _parse_minimal_yaml(text: str) -> dict[str, Any]:
    """Tiny fallback YAML parser for the example's flat `tool.yaml`s.

    Avoids a PyYAML dependency. Supports only:
      - top-level scalars (`name: foo`, `description: bar`)
      - single-level `parameters: { type: object, properties: {...} }`
    Anything richer: install PyYAML.
    """
    import re
    out: dict[str, Any] = {}
    for line in text.splitlines():
        line = line.rstrip()
        if not line or line.startswith("#") or line.startswith(" "):
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip()
    # If `parameters:` was multi-line, PyYAML is required.
    if "parameters" not in out and "type:" in text:
        raise ValueError(
            "PyYAML is required to load multi-line tool.yaml — pip install pyyaml"
        )
    return out


# ════════════════════════════════════════════════════════════════════
# PermissionRequestHandlerNode — bridges engine.permission_request to
# the existing in-process ApprovalRegistry + HTTP endpoints.
# ════════════════════════════════════════════════════════════════════


@dataclass
class _PendingApproval:
    request_id: str
    tool_name: str
    arguments: dict
    from_engine: NodeId
    correlation_id: str
    event: asyncio.Event = field(default_factory=asyncio.Event)
    decision: Optional[bool] = None  # None until /approve or /reject


class PermissionRequestHandlerNode:
    """Listens for engine-broadcast `permission_request` messages.

    For each request:
      1. registers it in the app's `ApprovalRegistry` (so the
         `GET /approvals` and `POST /approve/<id>` endpoints see it);
      2. waits on a per-request `asyncio.Event` (set by
         `decide(request_id, allow)` below);
      3. sends a `permission_response { allow, correlation_id }`
         reply to the engine.

    The engine's broadcast goes to every node on the bus; we use a
    single responder here and let others stay silent.
    """

    def __init__(self, bus: Bus, registry, node_id: str = "permission-handler") -> None:
        self.bus = bus
        self.registry = registry
        self._node_id = NodeId(node_id)
        self._node_id_str = node_id
        self._handle: Any = None
        self._listener: Optional[asyncio.Task] = None
        # request_id (engine-side payload `tool_call_id`) -> pending
        self._pending: dict[str, _PendingApproval] = {}
        # correlation_id -> request_id so the response reply can find it
        self._by_cid: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        info = NodeInfo(
            node_id=self._node_id_str,
            node_type="permission-handler",
            capabilities={"kind": "permission"},
            online_since=0,
        )
        # Broadcast reception so the engine's permission_request reaches us.
        flt = MessageFilter(
            types=["permission_request"],
            to_match=ToMatch.All,
        )
        self._handle = await self.bus.connect(info, flt)
        self._listener = asyncio.create_task(self._loop(), name="permission-handler")
        logger.info("PermissionRequestHandlerNode registered (node_id=%s)", self._node_id)

    def stop(self) -> None:
        if self._listener and not self._listener.done():
            self._listener.cancel()

    async def _loop(self) -> None:
        assert self._handle is not None
        while True:
            try:
                msg = await self._handle.recv()
            except Exception as e:
                logger.info("permission handler listener exiting: %s", e)
                return
            if msg.msg_type != "permission_request":
                continue
            await self._handle_request(msg)

    async def _handle_request(self, msg) -> None:
        payload = msg.payload
        if not hasattr(payload, "get"):
            return
        tool_name = payload.get("tool_name")
        arguments = payload.get("arguments", {}) or {}
        tool_call_id = payload.get("tool_call_id") or ""
        cid = payload.get("correlation_id") or ""
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except Exception:
                arguments = {"_raw": arguments}
        # Use the tool_call_id as the request id (it's the per-call unique
        # id from the model layer). Fall back to correlation_id if missing.
        request_id = tool_call_id or cid or f"req-{os.urandom(4).hex()}"
        pending = _PendingApproval(
            request_id=request_id,
            tool_name=tool_name or "",
            arguments=dict(arguments) if hasattr(arguments, "items") else {},
            from_engine=msg.sender,
            correlation_id=cid,
        )
        async with self._lock:
            self._pending[request_id] = pending
            if cid:
                self._by_cid[cid] = request_id
        # Register in the in-process ApprovalRegistry so the existing
        # /approvals HTTP endpoints surface it.
        self.registry.request(
            tool_name=pending.tool_name,
            params=pending.arguments,
            request_id=request_id,
        )
        logger.info(
            "permission_request pending (request_id=%s tool=%s engine=%s)",
            request_id, pending.tool_name, msg.sender,
        )
        # Wait for human decision.
        try:
            await asyncio.wait_for(pending.event.wait(), timeout=300.0)
        except asyncio.TimeoutError:
            logger.warning("permission_request %s timed out (denying)", request_id)
            pending.decision = False
        # Emit the reply.
        allow = bool(pending.decision)
        try:
            await self._handle.send(
                "permission_response",
                [msg.sender],
                {"allow": allow, "correlation_id": cid},
            )
        except Exception as e:
            logger.error("permission handler send failed: %s", e)
        # Cleanup.
        async with self._lock:
            self._pending.pop(request_id, None)
            if cid:
                self._by_cid.pop(cid, None)

    def decide(self, request_id: str, approved: bool) -> Optional[dict]:
        """Called from `POST /approve/<id>` (or `/reject/<id>`)."""
        pending = self._pending.get(request_id)
        if pending is None:
            return None
        pending.decision = approved
        pending.event.set()
        # Mirror into the registry: approved → keep entry (engine resumes
        # and runs the tool); rejected → drop pending (engine's tool
        # call returns a "denied" result).
        if not approved:
            self.registry.decide(request_id, approved=False)
            return None
        return self.registry._pending.get(request_id)
