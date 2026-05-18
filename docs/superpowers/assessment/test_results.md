# Framework Completeness — Empirical Test Results

Date: 2026-05-18
Framework version: main branch

Each scenario records:
- **User message**: Exact text used
- **Intent translation**: What UserAgent inferred
- **Handoff?**: Did handoff trigger correctly?
- **Result**: Pass / Fail / Partial / Blocked
- **Framework modification needed?**: Yes/No
- **Issues**: Any anomalies

---

## Group 1: Resource Self-Evolution (S1-S6)

### S1: "我想要个能查汇率的，输入币种和金额就行"

- **User message**: "我想要个能查汇率的，输入币种和金额就行"
- **Intent translation**: "用户想要一个汇率查询工具，输入源币种、目标币种和金额，返回实时汇率及换算结果"
- **Handoff?**: Yes -- handoff_to_sys triggered correctly with intent, required_actions, and reason
- **Result**: Pass (scaffold/design phase complete)
- **Framework modification needed?**: Yes -- two fixes required before test could run:
  1. `response_format: text` must be `response_format: {type: text}` (OpenAI SDK v1.6+)
  2. `Dispatcher._run_phase` used `len(traces)` as turn count, which counts trace events (~12 for 2 turns) instead of actual turns (2). Caused SysAgent to receive 0 remaining turns. Fixed with `_count_turns()`.
- **Issues**:
  - Initial server start failed because uvicorn was pointed at `arf.server` module instead of `ARFServer.app` instance. Resolved by using `ARFServer('default_workspace').start()`.
  - After fixing model config, the SysAgent successfully designed the tool with full schema (currency_converter, using frankfurter.app API, 3 parameters, error handling) and asked for user confirmation (Gate 1 pattern).
  - End-to-end write-to-workspace could not be completed in a single API call because the framework's design uses a multi-turn confirmation pattern (design -> confirm -> write). The agent design phase was successfully demonstrated.

### S2: "新建一个工具，帮我检测当前目录是不是git仓库"

- **User message**: "新建一个工具，帮我检测当前目录是不是git仓库"
- **Intent translation**: "用户想要创建一个新工具，功能是检测当前目录是否为 Git 仓库"
- **Handoff?**: Yes -- handoff_to_sys triggered with intent to create tool resource, write to tools/ path, and register
- **Result**: Pass (scaffold/design phase complete)
- **Framework modification needed?**: No (tested after S1 fixes)
- **Issues**:
  - SysAgent designed `git_repo_checker` tool with full parameter schema (path: optional string), return values for 3 scenarios (git repo, not git repo, path not found), using `subprocess.run(["git", "rev-parse"])` for detection.
  - Same multi-turn limitation as S1: design confirmed but file writing requires user confirmation in subsequent turn.

### S3: "帮我写一个 skill，每次会话结束后提取关键信息存到 long_term.md"

- **User message**: "帮我写一个 skill，每次会话结束后提取关键信息存到 long_term.md"
- **Intent translation**: "创建一个名为 session_memory_extractor 的 skill，功能是每次会话结束后自动提取对话中的关键信息并写入 long_term.md"
- **Handoff?**: Yes -- handoff_to_sys triggered correctly
- **Result**: Pass (scaffold/design phase complete)
- **Framework modification needed?**: No
- **Issues**:
  - SysAgent correctly analyzed existing `memory_extract` skill to differentiate the new skill (incremental per-turn vs. full-session summarization).
  - Designed `session_memory_extractor` with comparison table showing differences in trigger timing, scope, granularity, and output format.
  - Noted that the new skill would integrate with existing `memory_store` tool and `memory_extract` skill.
  - Same multi-turn confirmation limitation.

### S4: "注册一个新的模型，用硅基流动的 API"

- **User message**: "注册一个新的模型，用硅基流动的 API"
- **Intent translation**: "用户想要注册一个新的模型，使用硅基流动（SiliconFlow）的 API 密钥接入"
- **Handoff?**: Yes -- handoff_to_sys triggered correctly
- **Result**: Pass (discovery/planning phase complete)
- **Framework modification needed?**: No
- **Issues**:
  - SysAgent listed all 9 available model slots with their configuration status (3 configured, 6 unconfigured).
  - Identified SiliconFlow API base URL as `https://api.siliconflow.cn/v1`.
  - Noted that `model_configurator` skill needs activation first to guide the registration process.
  - Full registration would require user to provide API key in subsequent turns.

### S5: "更新我刚创建的那个汇率工具，加上历史汇率走势图功能"

- **User message**: "更新我刚创建的那个汇率工具，加上历史汇率走势图功能"
- **Intent translation**: N/A -- context not available
- **Handoff?**: No
- **Result**: Fail (expected -- no prior tool context)
- **Framework modification needed?**: Yes
  - The framework lacks long-term session memory that persists across sessions. Without it, references to previously created resources in new sessions cannot be resolved.
  - `session.md` and `long_term.md` exist but require specific setup/hooks to function as cross-session resource memory.
- **Issues**:
  - The UserAgent consumed all turns trying to find the "exchange rate tool" but found nothing in the workspace.
  - The `memory/` directory had no persistent record of the previous session's tool creation.
  - This is a designed limitation: the framework does not assume persistent resource memory across sessions.

### S6: "把那个汇率工具删掉吧，不用了"

- **User message**: "把那个汇率工具删掉吧，不用了"
- **Intent translation**: "用户想要删除一个名为汇率工具的资源"
- **Handoff?**: No
- **Result**: Partial (UserAgent correctly handled the "not found" case)
- **Framework modification needed?**: No (behavior is correct for the given context)
- **Issues**:
  - UserAgent correctly searched active tools, discoverable tools, tools/ directory, and long-term memory.
  - Found no matching resource and informed the user with a helpful message.
  - No handoff needed since the UserAgent's own tools (file_reader for directory listing) were sufficient for the lookup.
  - If the tool had existed, the handoff would have been needed for deletion (file_deleter with USER_RESTRICTED_PREFIXES).

---

## Server Start Attempt

### Initial attempt

The ARF server was started with `ARFServer('default_workspace').start(host='127.0.0.1', port=8000)` using Python 3.11.15.

**Diagnosis issues discovered:**

1. **Module resolution**: `uvicorn arf.server:app` fails because `arf.server` is a module, not a FastAPI app instance. The `ARFServer` class must be instantiated first. Workaround: use `ARFServer(...).start()`.

2. **Model config incompatibility**: Both `quick_thinking/config.yaml` and `quick_no_thinking/config.yaml` had `response_format: text` (plain string), but the installed OpenAI SDK (v1.6+) expects `response_format: {type: text}` (object). This caused HTTP 400 errors on every API call:
   ```
   response_format: invalid type: string "text", expected internally tagged enum ResponseFormat
   ```
   **Fix applied**: Changed to `response_format:\n    type: text` in both config files.

3. **Dispatcer turn counting bug**: `Dispatcher._run_phase()` calculated `turns = len(traces)` where `traces` is the list of trace events (each hook, call_model, classify, etc. generates a trace). For a UserAgent session with 2 actual turns, this produced ~12-16 "turns", causing `remaining_turns = max(1, 10 - 16) = 1` for the SysAgent. The SysAgent would then immediately hit the turn limit.
   **Fix applied**: Added `_count_turns()` method that extracts the actual `turn` value from trace events.

4. **OpenAI SDK compatibility**: The model configs use `thinking_enabled` + `reasoning_effort` which are translated to DeepSeek-specific `extra_body: {thinking: {type: enabled/disabled, effort: ...}}` in `ModelAdapter._build_api_params()`. This translation works correctly.

### Import verification

Before server start, the import chain was verified:
- `ResourceRegistry.load()` correctly loads 9 models, 19 tools, and 15 skills from system + workspace
- 3 DeepSeek models configured (deep_thinking: deepseek-v4-pro, quick_thinking/quick_no_thinking: deepseek-v4-flash)
- 6 model slots unconfigured (rerank, embedding, tts, stt, vision, vlm)

### Dependencies

- Python 3.11.15 (system Python is 3.6.8, incompatible with project requirement >=3.10)
- All dependencies pre-installed via `pip install -e ".[dev]"`
