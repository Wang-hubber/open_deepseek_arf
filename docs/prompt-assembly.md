# Prompt Assembly — System Prompt Assembly

## 1. OS Evolution

### 1.1 Analogy: Program Loader (execve / ELF Loader)

The operating system performs three tasks during `execve()`:
1. **Read the ELF header** — validate format, extract segment descriptors
2. **Map memory** — text (read-only, executable) + data (read-write) + bss (zero-initialized)
3. **Pass environment** — argc/argv/envp/auxv injected into the process address space

Prompt assembly is the Agent's execve:
1. **Read configuration** — `system_prompt.prefix` (role + critical_rules) + `system_prompt.suffix`
2. **Layered concatenation** — prefix (stable, targets API cache) + suffix (dynamic template)
3. **Placeholder substitution** — `$INVENTORY` (tool listing), `$MEMORY` (long-term resident memory), `$WORKSPACE` (workspace path, planned), `$TURN_BUDGET` (remaining turns, planned)

### 1.2 Why Stable Content Comes First

ELF places `.text` at the front and `.data`/`.bss` at the back. The same logic applies: LLM API prompt caching matches on prefix. `role` and `critical_rules` rarely change, so they always hit cache. The `suffix` contains `$INVENTORY` which changes when tools are updated — it sits in the second half, leaving the prefix cacheable.

| Component | ELF Analogy | Cache Behavior |
|-----------|-------------|----------------|
| `prefix.role` | `.text` (read-only, stable) | Always cached |
| `prefix.critical_rules` | `.rodata` (read-only data) | Always cached |
| `suffix` | `.data` / `.bss` (mutable) | Changes per-session, no cache |

### 1.3 Evolution Stages

| Stage | Problem | Solution |
|-------|---------|----------|
| v0.1 | Ad-hoc string concatenation | Bare `template` + `critical_rules` fields, no layering |
| v0.2 | No inversion of control | `SystemPromptProvider` Protocol supports DI override |
| v1.0 (current) | prefix/suffix layering + `string.Template` placeholders | `role` to `critical_rules` ordering guarantee, cache optimization |
| v1.1 (planned) | Multi-agent prompt composition, role-based template dispatch | See Section 3 |

---

## 2. Current Implementation

### 2.1 Configuration Models

```yaml
# agent.yaml
system_prompt:
  prefix:
    role: |
      You are arf_assistant, a helpful assistant.
    critical_rules: |
      ### R1: Verify with tools, never guess
      ### R2: Tool calls are action, not text
  suffix: |
    $INVENTORY
    $MEMORY
    Current workspace: $WORKSPACE
    Remaining turns: $TURN_BUDGET
```

```python
# arf/agent/config.py
class PrefixConfig(BaseModel):
    role: str = ""
    critical_rules: str = ""

class SystemPromptConfig(BaseModel):
    prefix: PrefixConfig = Field(default_factory=PrefixConfig)
    suffix: str = ""
```

**Field semantics:**

| Field | Cache Strategy | Content |
|-------|---------------|---------|
| `prefix.role` | Very stable | Role definition (who you are, capability boundaries) |
| `prefix.critical_rules` | Very stable | Hard rules (R1/R2/...), never to be violated |
| `suffix` | Variable | `$INVENTORY` / `$MEMORY` placeholder template |

### 2.2 Assembly Flow

```
agent.yaml                    DefaultSystemPromptProvider       BaseAgent
─────────                     ─────────────────────────         ─────────
system_prompt.prefix ───────→ build() ──→ SystemPrompt ──────→ $INVENTORY → MCP tool listing
       .role                        │         .prefix             $MEMORY    → resident memory (memory.md)
       .critical_rules              │         .suffix
       .suffix                      │
                                     │
                                     └── prefix: role + "\n\n" + critical_rules (ordering guarantee)
                                         suffix: passed through as-is (placeholders filled by BaseAgent)
```

### 2.3 DefaultSystemPromptProvider

```python
# arf/agent/default_prompt_provider.py
class DefaultSystemPromptProvider:
    def __init__(self, config: AgentConfig) -> None:
        self._config = config

    def build(self) -> SystemPrompt:
        sp = self._config.system_prompt
        pc = sp.prefix
        prefix_parts: list[str] = []
        if pc.role:
            prefix_parts.append(pc.role.strip())
        if pc.critical_rules:
            prefix_parts.append(pc.critical_rules.strip())
        prefix = "\n\n".join(prefix_parts)

        suffix = sp.suffix

        return SystemPrompt(prefix=prefix, suffix=suffix)
```

**Ordering guarantee:** `role` then `critical_rules`. The join is independent of field ordering in the YAML file.

### 2.4 SystemPrompt Value Object

```python
# arf/agent/prompt.py
@dataclass
class SystemPrompt:
    """Assembled system prompt with prefix/suffix separation.

    prefix — role + critical_rules (stable, target API cache)
    suffix — inventory + per-turn placeholders
    """
    prefix: str
    suffix: str

    @property
    def full_text(self) -> str:
        return self.prefix + self.suffix
```

### 2.5 Placeholder Mechanism

Uses Python `string.Template` syntax (`$VAR`). Two-stage replacement — both stages occur during `BaseAgent.__init__`:

| Placeholder | Replacement Timing | Replaced By | Cache Impact |
|-------------|-------------------|-------------|-------------|
| `$INVENTORY` | Session startup, after MCP connected | `BaseAgent.__init__` via `_build_inventory_from_mcp()` | Tool update triggers `resources/updated` notification |
| `$MEMORY` | Session startup, after resident memory loaded | `BaseAgent.__init__` via `_load_resident_memory()` | Should not change frequently (memory written at turn boundaries) |
| `$WORKSPACE` | Not yet implemented | — | Planned for ControlPlane per-turn replacement |
| `$TURN_BUDGET` | Not yet implemented | — | Planned for ControlPlane per-turn replacement |

**Why `string.Template`:**
- `$VAR` is shorter than `{{VAR}}`, reducing token consumption
- `$$` escapes to literal `$`
- Safer than `str.replace()` — `Template.safe_substitute()` does not raise on undefined variables

Current implementation uses simple `str.replace()` for startup-only placeholders. The `suffix` may contain `$WORKSPACE` and `$TURN_BUDGET` tokens that are not yet wired — they remain as literal text in the prompt until per-turn replacement is implemented in `ControlPlane` (see Section 3.3).

**Actual replacements in `BaseAgent.__init__`:**

1. `$INVENTORY` — filled by `_build_inventory_from_mcp()` (base.py:468-484). Queries `McpClientManager` for all available tool definitions and formats them as a Markdown list under `## Available Tools`. Returns empty string if MCP is not ready yet.

2. `$MEMORY` — filled by `_load_resident_memory()` (base.py:39-61). Reads `memory.md` from the workspace memory directory. Content is capped at `max_size_kb` (default 300 KB), with line-preserving truncation. Returns empty string if the file does not exist.

### 2.6 Protocol Interface

```python
# arf/core/protocols/prompt.py
class SystemPromptProvider(Protocol):
    def build(self) -> SystemPrompt:
        """Return assembled SystemPrompt with prefix/suffix populated."""
        ...
```

Application code injects a custom implementation via `override_protocols["system_prompt_provider"]`. Default is `DefaultSystemPromptProvider` using the `system_prompt` section of `AgentConfig`.

### 2.7 Code Paths

```
arf/agent/config.py:56-75              SystemPromptConfig + PrefixConfig (Pydantic models)
arf/agent/prompt.py:1-17               SystemPrompt value object
arf/agent/default_prompt_provider.py   DefaultSystemPromptProvider
arf/agent/base.py:299-321              BaseAgent assembly entry point, placeholder substitution
arf/agent/base.py:468-484              _build_inventory_from_mcp() — $INVENTORY from MCP tools
arf/agent/base.py:39-61                _load_resident_memory() — $MEMORY from memory.md
arf/core/protocols/prompt.py:9-13      SystemPromptProvider Protocol
```

---

## 3. Evolution Direction

### 3.1 Multi-Agent Prompt Composition

Currently each Agent independently configures `system_prompt`. Evolution: shared `base_prompt` + per-agent `delta`:

```yaml
base_prompt:
  prefix:
    role: "You are an ARF agent."
    critical_rules: |
      ### R1: Verify with tools
      ### R2: Tool calls are action

agents:
  - name: main_agent
    prompt_delta:
      prefix:
        role: "You handle user conversations."
  - name: sys_agent
    prompt_delta:
      prefix:
        role: "You handle system operations."
```

Merge strategy: `base + delta` — delta fields override base fields by name; unspecified fields inherit from base.

### 3.2 Role-Based Template Dispatch

Select prompt templates based on the Agent's `role` field, reducing duplicate configuration:

```yaml
prompt_templates:
  router:
    prefix:
      role: "You are a router agent."
      critical_rules: "### R4: Route to appropriate sub-agent..."
  builder:
    prefix:
      role: "You are a builder agent."
      critical_rules: "### Design first, then build."

agents:
  - name: main_agent
    template: router
  - name: sys_agent
    template: builder
```

### 3.3 Context-Aware Prompt (Per-Turn Placeholders)

Dynamically adjust the prompt based on session state — inject a `[Earlier]: ...` summary when `context_summary` is non-empty; inject error recovery hints when `tool_failures > 3`. This is where `$WORKSPACE` and `$TURN_BUDGET` per-turn replacement belongs — `ControlPlane` would update these before each `invoke()` or `astream()` iteration.

### 3.4 Prompt Versioning and A/B Testing

- Tag each prompt version with a hash; record `prompt_hash` in the trace
- Eval replay matches prompt versions to exclude prompt-change regression noise
- Support A/B testing: randomly select prompt variants within the same session, tagged in the trace
