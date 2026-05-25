# Dynamic Resource Reload — Design Spec

**Date:** 2026-05-25 | **Status:** approved

## 1. Motivation

Current state: ARF resources (tools/skills/models) are loaded once at agent startup and cached forever. Any change—adding a tool, editing a model config, deleting a skill—requires a full `/api/reload` that reconstructs the entire `BaseAgent`. The framework has no filesystem watch mechanism and no incremental cache invalidation.

Goal: resources are discovered from the filesystem dynamically, changes are detected automatically, and only the affected cache is invalidated.

## 2. Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Scope | tools + skills + models | All three resource types support dynamic discovery |
| Kernel marker | `activation: kernel` | Reuses existing field; kernel = framework built-in, read-only, cached permanently |
| FileWatcher | inotify + polling fallback | Native events on Linux, polling elsewhere; no mandatory external dependency |
| Reload timing | next turn | Safer than mid-turn mutation of tool definitions |
| agent.yaml role | optional override | Filesystem is source of truth; agent.yaml can override individual fields |
| File layout (tools) | `tools/{name}/` directory | Existing convention; each tool has `tool.yaml` + `function.py` |
| File layout (skills) | `skills/{name}.yaml` flat | Simple declarations, no implementation code |
| File layout (models) | `models/{name}.yaml` flat | Simple configs, no implementation code |

## 3. Architecture

### 3.1 Provider Layer

Three filesystem providers, each scanning a dedicated directory:

| Provider | Directory | Files | Produces |
|----------|-----------|-------|----------|
| `ToolProvider` | `tools/{name}/` | `tool.yaml` + `function.py` | `ToolConfig` + callable |
| `SkillProvider` | `skills/{name}.yaml` | single YAML file | `SkillConfig` |
| `ModelProvider` | `models/{name}.yaml` | single YAML file | `ModelConfig` |

Each provider:
- Scans at first `list()` call (lazy load)
- Returns cached results for subsequent calls
- Cache is split: kernel entries frozen after init, dynamic entries cleared on filesystem change

### 3.2 ResourceCache

```
ResourceCache
├── kernel: dict   ← populated at BaseAgent.__init__, never cleared
└── dynamic: dict  ← lazy-loaded, cleared on fs change
```

- `kernel`: resources with `activation: kernel`. Framework built-in tools (file_reader, web_search, etc.) go here. Read-only to users.
- `dynamic`: everything else. Invalidated when FileWatcher fires, re-populated lazily on next access.

### 3.3 FileWatcher

A cross-platform filesystem watcher with dual-track implementation:

- **Linux:** `inotify` via stdlib's `select` or `inotify_simple` — sub-second detection
- **Other platforms:** polling loop, `os.stat` every 5 seconds, compare mtime

Configuration (in `advanced:`):
```yaml
advanced:
  reload:
    watch: true          # enable FileWatcher
    poll_interval: 5     # seconds, polling mode only
```

Unified interface: `FileWatcher.add(path, callback)`.

### 3.4 Reload Flow

```
filesystem change (write / delete / rename)
  │
  ▼
FileWatcher detects event
  │
  ▼
ResourceCache.dynamic.clear()
  │
  ▼
next engine turn → get_tool_definitions() / get_skill_definitions() / get_model_definitions()
  │
  ▼
provider._load_all() re-scans, populates dynamic cache
  │
  ▼
agent.yaml overrides are applied (merge on read)
  │
  ▼
model receives updated function definitions
```

## 4. agent.yaml Relationship

### 4.1 Priority

Filesystem definition is the base. `agent.yaml` fields override individual fields of matching resources.

Example: `models/quick.yaml` has `temperature: 0.7`. `agent.yaml` declares:
```yaml
models:
  - name: quick
    temperature: 0.3
```
Final value: `temperature: 0.3`.

### 4.2 Omission is Allowed

Users may omit `models:`, `skills:`, or `tools:` from `agent.yaml` entirely. When omitted, everything is discovered from the filesystem with default values.

### 4.3 Config Generation

`POST /api/resources/generate-config` or CLI `arf config generate` scans the filesystem and writes a complete `agent.yaml` with all discovered resources. The user edits this file only for overrides.

## 5. API Changes

| Endpoint | Current | New |
|----------|---------|-----|
| `POST /api/reload` | full agent rebuild | unchanged for agent-level config changes (name, system_prompt, etc.) |
| `POST /api/resources/reload` | does not exist | clear dynamic cache, force re-scan all providers |
| `GET /api/resources/generate-config` | does not exist | scan filesystem, return complete agent.yaml text |
| `/api/resources/models/{name}` | returns masked key | unchanged |
| `/api/resources/{type}` | lists config-based items | lists filesystem-discovered items merged with agent.yaml overrides |

## 6. Migration

On first load with the new system:
1. If `agent.yaml` has `models:`/`skills:`/`tools:` defined, they serve as overrides (existing projects work without changes)
2. If filesystem lacks corresponding files, the agent.yaml definitions are treated as full definitions (backward compatible)
3. New projects can omit these sections entirely and rely solely on filesystem discovery

## 7. Testability

- `FileWatcher` accepts a mock clock for deterministic polling tests
- `ResourceCache` can be instantiated standalone for unit tests
- Providers accept a `fs_root` parameter, defaulting to `./`, overridable in tests
- `BaseAgent` accepts `watch_enabled=False` to disable FileWatcher in test mode
