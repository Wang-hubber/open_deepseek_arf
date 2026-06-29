# 任务 5.10：测试 fixtures（ScriptTool 脚本）

> Phase 5 — MCP 第十项任务
> 父文档：`docs/v1.x/phase5_mcp/phase5-mcp-design.md`
> 依赖：Task 5.2 (ScriptTool), Task 5.5 (McpNode 统一)

## 设计思路

按 Phase 5 设计文档第 "测试基准工具" 节的规格，创建三个 ScriptTool fixtures——`read_file`、`write_file`、`search_content`——以 Python 脚本 + `tool.toml` 形式放在 `tests/fixtures/` 目录下。

这三个 fixture 是**真实可用的工具**——不是 mock/echo。`read_file` 真的读文件，`write_file` 真的写文件，`search_content` 真的做正则搜索。它们：

1. 被 DiscoveryModule scan 发现和注册
2. 被 ScriptTool 包裹，走 `stdin/stdout JSON` 协议
3. 用于 Task 5.12 集成测试（验证 LocalMcpNode 完整链路：扫描 → 发现 → 执行 → 结果）

**设计约束**：
- 遵循 `stdin JSON → stdout JSON` 协议，`stderr` 为错误输出
- 不做路径安全检查——sandbox 是框架层职责（`PathCheckToolGuard`），工具层不需要重复校验
- 错误通过 `{"ok": false, "error": "..."}` JSON 返回（而非非零退出码），保证 ScriptTool 能解析 stdout 为合法 JSON 并携带结构化错误信息

### 目录结构

每个工具提供 Python、Bash、Rust 三种 runtime 实现，验证 ScriptTool 对三种 ScriptRuntime 的完整支持：

```
crates/arf-mcp/tests/fixtures/
├── read_file/
│   ├── tool.toml        # runtime = "python"
│   └── main.py
├── read_file_bash/
│   ├── tool.toml        # runtime = "bash"
│   └── main.sh
├── read_file_rust/
│   ├── tool.toml        # runtime = "rust"
│   └── main.rs
├── write_file/
│   ├── tool.toml        # runtime = "python"
│   └── main.py
├── write_file_bash/
│   ├── tool.toml        # runtime = "bash"
│   └── main.sh
├── write_file_rust/
│   ├── tool.toml        # runtime = "rust"
│   └── main.rs
├── search_content/
│   ├── tool.toml        # runtime = "python"
│   └── main.py
├── search_content_bash/
│   ├── tool.toml        # runtime = "bash"
│   └── main.sh
├── search_content_rust/
│   ├── tool.toml        # runtime = "rust"
│   └── main.rs
```

| 文件 | 操作 | 内容 |
|------|------|------|
| `tests/fixtures/read_file/tool.toml` | 新建 | Python read_file |
| `tests/fixtures/read_file/main.py` | 新建 | 读取文件内容 (Python) |
| `tests/fixtures/read_file_bash/tool.toml` | 新建 | Bash read_file |
| `tests/fixtures/read_file_bash/main.sh` | 新建 | 读取文件内容 (Bash) |
| `tests/fixtures/read_file_rust/tool.toml` | 新建 | Rust read_file |
| `tests/fixtures/read_file_rust/main.rs` | 新建 | 读取文件内容 (Rust) |
| `tests/fixtures/write_file/tool.toml` | 新建 | Python write_file |
| `tests/fixtures/write_file/main.py` | 新建 | 写入文件内容 (Python) |
| `tests/fixtures/write_file_bash/tool.toml` | 新建 | Bash write_file |
| `tests/fixtures/write_file_bash/main.sh` | 新建 | 写入文件内容 (Bash) |
| `tests/fixtures/write_file_rust/tool.toml` | 新建 | Rust write_file |
| `tests/fixtures/write_file_rust/main.rs` | 新建 | 写入文件内容 (Rust) |
| `tests/fixtures/search_content/tool.toml` | 新建 | Python search_content |
| `tests/fixtures/search_content/main.py` | 新建 | 正则搜索 (Python) |
| `tests/fixtures/search_content_bash/tool.toml` | 新建 | Bash search_content |
| `tests/fixtures/search_content_bash/main.sh` | 新建 | 正则搜索 (Bash, via grep) |
| `tests/fixtures/search_content_rust/tool.toml` | 新建 | Rust search_content |
| `tests/fixtures/search_content_rust/main.rs` | 新建 | 子串搜索 (Rust, 仅 std) |

---

## 代码实现

### `read_file/tool.toml`

```toml
name = "read_file"
description = "Read the contents of a file at the given path. Returns the file content as a string."
runtime = "python"
entrypoint = "main.py"
timeout_ms = 10000

[params_schema]
type = "object"
properties.path = { type = "string", description = "Absolute path to the file to read" }
required = ["path"]
```

### `read_file/main.py`

逐行解释：

```python
#!/usr/bin/env python3
import sys                          # stdin/stdout 读写
import json                         # JSON 协议编解码
from pathlib import Path            # 现代路径 API


def main():
    # 1. 从 stdin 读取 JSON params
    try:
        params = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "error": f"invalid JSON params: {e}"}))
        sys.exit(0)

    path_str = params.get("path", "")
    if not path_str:
        print(json.dumps({"ok": False, "error": "missing required param: path"}))
        sys.exit(0)

    # 2. 检查文件是否存在并可读
    file_path = Path(path_str)
    if not file_path.exists():
        print(json.dumps({"ok": False, "error": f"file not found: {path_str}"}))
        sys.exit(0)

    if not file_path.is_file():
        print(json.dumps({"ok": False, "error": f"not a file: {path_str}"}))
        sys.exit(0)

    # 3. 读取内容并返回
    try:
        content = file_path.read_text(encoding="utf-8")
    except PermissionError:
        print(json.dumps({"ok": False, "error": f"permission denied: {path_str}"}))
        sys.exit(0)
    except UnicodeDecodeError:
        print(json.dumps({"ok": False, "error": f"binary file not supported: {path_str}"}))
        sys.exit(0)
    except OSError as e:
        print(json.dumps({"ok": False, "error": f"read error: {e}"}))
        sys.exit(0)

    # 4. stdout 输出 JSON result
    print(json.dumps({"ok": True, "content": content, "path": path_str}))


if __name__ == "__main__":
    main()
```

**关键设计决策**：
- `sys.exit(0)` 而非非零退出码——保证 ScriptTool 解析 stdout 成功（stdout 输出的是合法 JSON 对象 `{"ok": false, "error": "..."}`），错误信息通过结构化 JSON 字段传递
- 使用 `pathlib.Path` 而非 `os.path`——Python 3.6+，更清晰
- `UnicodeDecodeError` 单独捕获——二进制文件不应该被当成文本读取，返回明确错误信息

---

### `write_file/tool.toml`

```toml
name = "write_file"
description = "Write content to a file at the given path. Creates parent directories if they don't exist."
runtime = "python"
entrypoint = "main.py"
timeout_ms = 10000

[params_schema]
type = "object"
properties.path = { type = "string", description = "Absolute path to the file to write" }
properties.content = { type = "string", description = "Content to write to the file" }
required = ["path", "content"]
```

### `write_file/main.py`

逐行解释：

```python
#!/usr/bin/env python3
import sys
import json
from pathlib import Path


def main():
    try:
        params = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "error": f"invalid JSON params: {e}"}))
        sys.exit(0)

    path_str = params.get("path", "")
    content = params.get("content", "")
    if not path_str:
        print(json.dumps({"ok": False, "error": "missing required param: path"}))
        sys.exit(0)

    file_path = Path(path_str)

    # 1. 确保父目录存在
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        print(json.dumps({"ok": False, "error": f"permission denied creating parent: {file_path.parent}"}))
        sys.exit(0)
    except OSError as e:
        print(json.dumps({"ok": False, "error": f"mkdir error: {e}"}))
        sys.exit(0)

    # 2. 写入文件内容
    try:
        bytes_written = file_path.write_text(content, encoding="utf-8")
    except PermissionError:
        print(json.dumps({"ok": False, "error": f"permission denied: {path_str}"}))
        sys.exit(0)
    except OSError as e:
        print(json.dumps({"ok": False, "error": f"write error: {e}"}))
        sys.exit(0)

    # 3. 返回结果
    print(json.dumps({
        "ok": True,
        "path": path_str,
        "bytes": len(content.encode("utf-8")),
    }))


if __name__ == "__main__":
    main()
```

**关键设计决策**：
- `mkdir(parents=True, exist_ok=True)`——自动创建父目录，`exist_ok=True` 保证幂等
- 返回 `bytes`（UTF-8 编码后的字节数）而非字符数——与 `wc -c` 一致，便于调用方校验

---

### `search_content/tool.toml`

```toml
name = "search_content"
description = "Search for a regex pattern in files under a directory. Returns matching lines with file path, line number, and content."
runtime = "python"
entrypoint = "main.py"
timeout_ms = 30000

[params_schema]
type = "object"
properties.pattern = { type = "string", description = "Regex pattern to search for" }
properties.path = { type = "string", description = "Directory path to search in" }
required = ["pattern", "path"]
```

### `search_content/main.py`

逐行解释：

```python
#!/usr/bin/env python3
import sys
import json
import re
from pathlib import Path

# 不搜索的目录名（避免在 .git/ 等中无意义搜索）
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "target", ".mypy_cache", ".pytest_cache"}


def main():
    try:
        params = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "error": f"invalid JSON params: {e}"}))
        sys.exit(0)

    pattern_str = params.get("pattern", "")
    dir_str = params.get("path", "")

    if not pattern_str:
        print(json.dumps({"ok": False, "error": "missing required param: pattern"}))
        sys.exit(0)
    if not dir_str:
        print(json.dumps({"ok": False, "error": "missing required param: path"}))
        sys.exit(0)

    search_dir = Path(dir_str)
    if not search_dir.exists():
        print(json.dumps({"ok": False, "error": f"directory not found: {dir_str}"}))
        sys.exit(0)
    if not search_dir.is_dir():
        print(json.dumps({"ok": False, "error": f"not a directory: {dir_str}"}))
        sys.exit(0)

    # 编译正则
    try:
        pattern = re.compile(pattern_str)
    except re.error as e:
        print(json.dumps({"ok": False, "error": f"invalid regex: {e}"}))
        sys.exit(0)

    matches = []
    max_matches = 100  # 防止超大输出

    # 递归遍历目录
    for file_path in search_dir.rglob("*"):
        # 跳过不需要的目录
        if any(skip in file_path.parts for skip in SKIP_DIRS):
            continue

        if not file_path.is_file():
            continue

        # 尝试作为文本文件读取
        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, OSError):
            continue

        for line_no, line in enumerate(content.splitlines(), start=1):
            if pattern.search(line):
                matches.append({
                    "file": str(file_path),
                    "line": line_no,
                    "content": line.strip(),
                })
                if len(matches) >= max_matches:
                    print(json.dumps({
                        "ok": True,
                        "matches": matches,
                        "truncated": True,
                    }))
                    sys.exit(0)

    print(json.dumps({"ok": True, "matches": matches}))


if __name__ == "__main__":
    main()
```

**关键设计决策**：
- `SKIP_DIRS`——避免搜索 `.git`、`node_modules` 等无关目录，大幅减少无关结果和性能开销
- `max_matches = 100`——防止超大输出导致 Tool Output Externalization 截断；达到上限后标记 `truncated: true`
- 二进制文件 (`UnicodeDecodeError`) 静默跳过——不做启发式编码检测，工具保持简单
- 返回 `file` 使用 `str(file_path)` 即绝对路径——调用方不需要拼接 base_dir

---

## Bash 实现

> Bash 的 stdin/stdout JSON 协议有一个天然矛盾：Bash 没有原生 JSON 解析能力。解决方案：用 `python3 -c` 做 JSON 编解码胶水（~5 行），核心文件操作使用原生 Bash 命令。
>
> 这是务实的工程选择——不假装 Bash 能做 JSON，也不因为 JSON 限制而放弃 Bash 在文件操作上的表达力。

### `read_file_bash/tool.toml`

```toml
name = "read_file"
description = "Read the contents of a file at the given path. Returns the file content as a string."
runtime = "bash"
entrypoint = "main.sh"
timeout_ms = 10000

[params_schema]
type = "object"
properties.path = { type = "string", description = "Absolute path to the file to read" }
required = ["path"]
```

### `read_file_bash/main.sh`

逐行解释：

```bash
#!/usr/bin/env bash
set -euo pipefail

# ── JSON input parsing (bash has no native JSON — python3 glue) ──
PATH_VAL=$(python3 -c "
import json,sys
d = json.loads(sys.stdin.read())
print(d.get('path', ''))
" 2>/dev/null || true)

if [ -z "$PATH_VAL" ]; then
    python3 -c "import json; print(json.dumps({'ok': False, 'error': 'missing required param: path'}))"
    exit 0
fi

# ── File operations (native bash) ──
if [ ! -e "$PATH_VAL" ]; then
    python3 -c "import json; print(json.dumps({'ok': False, 'error': 'file not found: $PATH_VAL'}))"
    exit 0
fi

if [ ! -f "$PATH_VAL" ]; then
    python3 -c "import json; print(json.dumps({'ok': False, 'error': 'not a file: $PATH_VAL'}))"
    exit 0
fi

if [ ! -r "$PATH_VAL" ]; then
    python3 -c "import json; print(json.dumps({'ok': False, 'error': 'permission denied: $PATH_VAL'}))"
    exit 0
fi

# 用 cat 读内容，python3 做 JSON 编码（保证特殊字符正确转义）
CONTENT=$(cat "$PATH_VAL")
python3 -c "
import json
content = open('$PATH_VAL').read()
print(json.dumps({'ok': True, 'content': content, 'path': '$PATH_VAL'}))
"
```

**关键设计决策**：
- JSON 编解码用 `python3 -c` 一行胶水——核心文件操作（`[ -e ]`、`[ -f ]`、`[ -r ]`、`cat`）全部原生 Bash
- `set -euo pipefail`——未定义变量/管道失败立即退出，防御性编程
- `2>/dev/null || true`——python3 解析失败时 `PATH_VAL` 为空，触发 missing param 分支

### `write_file_bash/tool.toml`

```toml
name = "write_file"
description = "Write content to a file at the given path. Creates parent directories if they don't exist."
runtime = "bash"
entrypoint = "main.sh"
timeout_ms = 10000

[params_schema]
type = "object"
properties.path = { type = "string", description = "Absolute path to the file to write" }
properties.content = { type = "string", description = "Content to write to the file" }
required = ["path", "content"]
```

### `write_file_bash/main.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

# ── JSON input parsing ──
eval "$(python3 -c "
import json,sys
d = json.loads(sys.stdin.read())
print(f'PATH_VAL={d.get(\"path\", \"\")!r}')
content = d.get('content', '')
print(f'CONTENT={content!r}')
" 2>/dev/null || echo 'PARSE_ERROR=1')"

if [ -n "${PARSE_ERROR:-}" ]; then
    python3 -c "import json; print(json.dumps({'ok': False, 'error': 'invalid JSON params'}))"
    exit 0
fi

if [ -z "$PATH_VAL" ]; then
    python3 -c "import json; print(json.dumps({'ok': False, 'error': 'missing required param: path'}))"
    exit 0
fi

# ── File operations (native bash) ──
DIR=$(dirname "$PATH_VAL")
if [ ! -d "$DIR" ]; then
    mkdir -p "$DIR" 2>/dev/null || {
        python3 -c "import json; print(json.dumps({'ok': False, 'error': 'mkdir error: $DIR'}))"
        exit 0
    }
fi

# 写入内容
printf '%s' "$CONTENT" > "$PATH_VAL" 2>/dev/null || {
    python3 -c "import json; print(json.dumps({'ok': False, 'error': 'write error: $PATH_VAL'}))"
    exit 0
}

# ── JSON output ──
BYTES=$(wc -c < "$PATH_VAL" | tr -d ' ')
python3 -c "
import json
print(json.dumps({'ok': True, 'path': '$PATH_VAL', 'bytes': $BYTES}))
"
```

**关键设计决策**：
- `printf '%s'` 而非 `echo`——`echo` 会转义反斜杠和 `-n` 等问题，`printf '%s'` 原样输出
- `wc -c` 返回字节数（与 Python 版 `len(content.encode("utf-8"))` 对齐）
- `eval` + `!r` 做安全 shell 转义——Python 的 `repr()` 自动处理引号、换行符等特殊字符

### `search_content_bash/tool.toml`

```toml
name = "search_content"
description = "Search for a regex pattern in files under a directory. Returns matching lines with file path, line number, and content."
runtime = "bash"
entrypoint = "main.sh"
timeout_ms = 30000

[params_schema]
type = "object"
properties.pattern = { type = "string", description = "Regex pattern to search for" }
properties.path = { type = "string", description = "Directory path to search in" }
required = ["pattern", "path"]
```

### `search_content_bash/main.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

# ── JSON input parsing ──
eval "$(python3 -c "
import json,sys
d = json.loads(sys.stdin.read())
print(f'PATTERN={d.get(\"pattern\", \"\")!r}')
print(f'SEARCH_DIR={d.get(\"path\", \"\")!r}')
" 2>/dev/null || echo 'PARSE_ERROR=1')"

if [ -n "${PARSE_ERROR:-}" ]; then
    python3 -c "import json; print(json.dumps({'ok': False, 'error': 'invalid JSON params'}))"
    exit 0
fi

if [ -z "$PATTERN" ]; then
    python3 -c "import json; print(json.dumps({'ok': False, 'error': 'missing required param: pattern'}))"
    exit 0
fi

if [ -z "$SEARCH_DIR" ]; then
    python3 -c "import json; print(json.dumps({'ok': False, 'error': 'missing required param: path'}))"
    exit 0
fi

if [ ! -d "$SEARCH_DIR" ]; then
    python3 -c "import json; print(json.dumps({'ok': False, 'error': 'directory not found: $SEARCH_DIR'}))"
    exit 0
fi

# ── Search (native grep) ──
# -r recursive, -n line numbers, -I skip binary, --include for text files only
# Suppress grep's own exit code 1 (no matches)
MATCHES=$(grep -rnI --include='*.rs' --include='*.py' --include='*.sh' \
    --include='*.toml' --include='*.md' --include='*.txt' \
    --include='*.json' --include='*.yaml' --include='*.yml' \
    --include='*.js' --include='*.ts' --include='*.tsx' \
    --include='*.html' --include='*.css' \
    --exclude-dir='.git' --exclude-dir='__pycache__' \
    --exclude-dir='node_modules' --exclude-dir='.venv' \
    --exclude-dir='venv' --exclude-dir='target' \
    --exclude-dir='.mypy_cache' --exclude-dir='.pytest_cache' \
    -e "$PATTERN" "$SEARCH_DIR" 2>/dev/null || true)

if [ -z "$MATCHES" ]; then
    python3 -c "import json; print(json.dumps({'ok': True, 'matches': []}))"
    exit 0
fi

# ── Format output as JSON ──
# grep output format: path:line:content
# python3 handles JSON encoding (escaping special chars in content)
python3 -c "
import json, sys

lines = sys.argv[1].split('\n')
max_matches = 100
matches = []
for line in lines:
    if not line.strip():
        continue
    # Split on first two colons: file:lineno:content
    parts = line.split(':', 2)
    if len(parts) >= 3:
        matches.append({
            'file': parts[0],
            'line': int(parts[1]),
            'content': parts[2].strip(),
        })
    if len(matches) >= max_matches:
        print(json.dumps({'ok': True, 'matches': matches, 'truncated': True}))
        sys.exit(0)

print(json.dumps({'ok': True, 'matches': matches}))
" "$MATCHES"
```

**关键设计决策**：
- 核心搜索用原生 `grep -rnI`——比 Python 遍历 + 正则更快，且是 Bash 最自然的文本搜索方式
- `--include` 白名单——只搜索文本文件类型，避免在二进制文件中搜索
- `--exclude-dir` 跳过无关目录（与 Python 版 `SKIP_DIRS` 对齐）
- grep 无匹配时返回 exit code 1，用 `|| true` 抑制，空结果由空检测分支处理
- 结果格式化用 `python3 -c`——`grep` 的 `path:line:content` 格式含特殊字符（`"`、`\n`），手工拼 JSON 易出错

### Bash 实现的 JSON 胶水模式

三种 Bash 脚本共享同一模式：

| 步骤 | 实现 | 原因 |
|------|------|------|
| JSON 输入解析 | `python3 -c "json.loads(sys.stdin.read())"` | Bash 无原生 JSON |
| 文件操作 | `cat` / `mkdir -p` / `grep -rnI` | Bash 原生优势 |
| JSON 输出编码 | `python3 -c "json.dumps({...})"` | 保证特殊字符正确转义 |

每处 JSON 胶水约 3-5 行，核心业务逻辑全在 Bash 原生命令。这是一种诚实的折中——不强行用 Bash 做 JSON 解析（那会是灾难），也不因为 stdin/stdout JSON 协议而放弃 Bash。

---

## Rust 实现

> Rust 通过 `rustc` 直接编译（无 Cargo），不能使用外部 crate。`serde_json` 不可用，需手写最小 JSON 解析。
>
> 对于这三个工具，输入 JSON 是简单的一层扁平对象（field: string），用一个约 60 行的 `extract_str()` 函数做最小 JSON 字符串提取——不处理嵌套、数组、数字、布尔，刚好覆盖三种工具的 params 格式。
>
> 实际工程中 Rust 脚本如果依赖复杂 JSON，建议改用 Python 或支持 `cargo` 编译——`rustc` 适合性能关键且输入简单的场景。

### `read_file_rust/tool.toml`

```toml
name = "read_file"
description = "Read the contents of a file at the given path. Returns the file content as a string."
runtime = "rust"
entrypoint = "main.rs"
timeout_ms = 10000

[params_schema]
type = "object"
properties.path = { type = "string", description = "Absolute path to the file to read" }
required = ["path"]
```

### `read_file_rust/main.rs`

逐行解释：

```rust
use std::fs;
use std::io::{self, Read};

/// Extract the value of a string field from flat JSON like {"key": "value"}.
/// Returns None if the key is absent or value is not a string.
fn extract_str(json: &str, key: &str) -> Option<String> {
    let search = format!("\"{}\"", key);
    let pos = json.find(&search)?;
    // scan past '"key"'
    let after_key = &json[pos + search.len()..];
    // find the ':'
    let colon = after_key.find(':')?;
    let after_colon = &after_key[colon + 1..];
    // skip whitespace
    let trimmed = after_colon.trim_start();
    // find opening '"'
    if !trimmed.starts_with('"') {
        return None;
    }
    let inner = &trimmed[1..];
    // find closing '"' (unescaped)
    let mut result = String::new();
    let mut chars = inner.chars();
    loop {
        match chars.next() {
            None => return None,          // unterminated string
            Some('\\') => {
                match chars.next() {
                    Some('"') => result.push('"'),
                    Some('\\') => result.push('\\'),
                    Some('n') => result.push('\n'),
                    Some('t') => result.push('\t'),
                    Some('r') => result.push('\r'),
                    Some(c) => { result.push('\\'); result.push(c); }
                    None => return None,
                }
            }
            Some('"') => break,            // closing quote
            Some(c) => result.push(c),
        }
    }
    Some(result)
}

fn main() {
    // 1. Read all of stdin
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let input = input.trim();

    // 2. Extract "path" field
    let path_str = match extract_str(input, "path") {
        Some(p) if !p.is_empty() => p,
        _ => {
            println!(r#"{{"ok": false, "error": "missing required param: path"}}"#);
            return;
        }
    };

    // 3. Check file exists & readable
    let metadata = match fs::metadata(&path_str) {
        Ok(m) => m,
        Err(_) => {
            println!(r#"{{"ok": false, "error": "file not found: {}"}}"#, path_str);
            return;
        }
    };
    if !metadata.is_file() {
        println!(r#"{{"ok": false, "error": "not a file: {}"}}"#, path_str);
        return;
    }

    // 4. Read file content
    match fs::read_to_string(&path_str) {
        Ok(content) => {
            // Escape content for JSON string embedding
            let escaped = content
                .replace('\\', "\\\\")
                .replace('"', "\\\"")
                .replace('\n', "\\n")
                .replace('\r', "\\r")
                .replace('\t', "\\t");
            println!(
                r#"{{"ok": true, "content": "{}", "path": "{}"}}"#,
                escaped, path_str
            );
        }
        Err(e) => {
            println!(
                r#"{{"ok": false, "error": "read error: {}"}}"#, e
            );
        }
    };
}
```

### `write_file_rust/tool.toml`

```toml
name = "write_file"
description = "Write content to a file at the given path. Creates parent directories if they don't exist."
runtime = "rust"
entrypoint = "main.rs"
timeout_ms = 10000

[params_schema]
type = "object"
properties.path = { type = "string", description = "Absolute path to the file to write" }
properties.content = { type = "string", description = "Content to write to the file" }
required = ["path", "content"]
```

### `write_file_rust/main.rs`

```rust
use std::fs;
use std::io::{self, Read};
use std::path::Path;

// (extract_str same as read_file_rust — included for standalone compilation)
fn extract_str(json: &str, key: &str) -> Option<String> {
    let search = format!("\"{}\"", key);
    let pos = json.find(&search)?;
    let after_key = &json[pos + search.len()..];
    let colon = after_key.find(':')?;
    let after_colon = &after_key[colon + 1..];
    let trimmed = after_colon.trim_start();
    if !trimmed.starts_with('"') { return None; }
    let inner = &trimmed[1..];
    let mut result = String::new();
    let mut chars = inner.chars();
    loop {
        match chars.next() {
            None => return None,
            Some('\\') => match chars.next() {
                Some('"') => result.push('"'),
                Some('\\') => result.push('\\'),
                Some('n') => result.push('\n'),
                Some('t') => result.push('\t'),
                Some('r') => result.push('\r'),
                Some(c) => { result.push('\\'); result.push(c); }
                None => return None,
            },
            Some('"') => break,
            Some(c) => result.push(c),
        }
    }
    Some(result)
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let input = input.trim();

    let path_str = match extract_str(input, "path") {
        Some(p) if !p.is_empty() => p,
        _ => {
            println!(r#"{{"ok": false, "error": "missing required param: path"}}"#);
            return;
        }
    };
    let content = extract_str(input, "content").unwrap_or_default();

    // 1. Create parent directories
    if let Some(parent) = Path::new(&path_str).parent() {
        if !parent.as_os_str().is_empty() {
            if let Err(e) = fs::create_dir_all(parent) {
                println!(r#"{{"ok": false, "error": "mkdir error: {}"}}"#, e);
                return;
            }
        }
    }

    // 2. Write file
    match fs::write(&path_str, &content) {
        Ok(()) => {
            let bytes = content.len();
            println!(
                r#"{{"ok": true, "path": "{}", "bytes": {}}}"#,
                path_str, bytes
            );
        }
        Err(e) => {
            println!(r#"{{"ok": false, "error": "write error: {}"}}"#, e);
        }
    };
}
```

**关键设计决策**：
- `content.len()` 返回字节数（Rust `String::len()` 本身就是字节数），与 Python 版 `len(content.encode("utf-8"))` 一致
- 错误路径通过 `println!()` 输出 JSON，正常退出（不 panic）——与 Python 版行为一致

### `search_content_rust/tool.toml`

```toml
name = "search_content"
description = "Search for a substring in files under a directory. Returns matching lines with file path, line number, and content. Note: uses substring matching, not regex (rustc-only, no regex crate available)."
runtime = "rust"
entrypoint = "main.rs"
timeout_ms = 30000

[params_schema]
type = "object"
properties.pattern = { type = "string", description = "Substring to search for (exact match, not regex)" }
properties.path = { type = "string", description = "Directory path to search in" }
required = ["pattern", "path"]
```

### `search_content_rust/main.rs`

```rust
use std::fs;
use std::io::{self, Read};
use std::path::Path;

// (extract_str same as above)
fn extract_str(json: &str, key: &str) -> Option<String> { /* ... same impl ... */ }

const SKIP_DIRS: &[&str] = &[
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "target", ".mypy_cache", ".pytest_cache",
];
const MAX_MATCHES: usize = 100;

/// Escape a string for JSON embedding.
fn json_escape(s: &str) -> String {
    s.replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('\n', "\\n")
        .replace('\r', "\\r")
        .replace('\t', "\\t")
}

/// Walk directory recursively, searching files for pattern.
fn search_dir(dir: &Path, pattern: &str, matches: &mut Vec<String>) -> io::Result<()> {
    if !dir.is_dir() {
        return Ok(());
    }

    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        let path = entry.path();
        let file_name = path.file_name().unwrap_or_default().to_string_lossy();

        if path.is_dir() {
            if SKIP_DIRS.contains(&file_name.as_ref()) {
                continue;
            }
            search_dir(&path, pattern, matches)?;
        } else if path.is_file() {
            if matches.len() >= MAX_MATCHES {
                return Ok(());
            }
            // Try reading as text
            let content = match fs::read_to_string(&path) {
                Ok(c) => c,
                Err(_) => continue,
            };
            for (line_no, line) in content.lines().enumerate() {
                if matches.len() >= MAX_MATCHES {
                    break;
                }
                if line.contains(pattern) {
                    let file = path.to_string_lossy();
                    let escaped_line = json_escape(line.trim());
                    matches.push(format!(
                        r#"{{"file": "{}", "line": {}, "content": "{}"}}"#,
                        file, line_no + 1, escaped_line
                    ));
                }
            }
        }
    }
    Ok(())
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let input = input.trim();

    let pattern = match extract_str(input, "pattern") {
        Some(p) if !p.is_empty() => p,
        _ => {
            println!(r#"{{"ok": false, "error": "missing required param: pattern"}}"#);
            return;
        }
    };
    let dir_str = match extract_str(input, "path") {
        Some(p) if !p.is_empty() => p,
        _ => {
            println!(r#"{{"ok": false, "error": "missing required param: path"}}"#);
            return;
        }
    };

    let search_path = Path::new(&dir_str);
    if !search_path.exists() {
        println!(r#"{{"ok": false, "error": "directory not found: {}"}}"#, dir_str);
        return;
    }
    if !search_path.is_dir() {
        println!(r#"{{"ok": false, "error": "not a directory: {}"}}"#, dir_str);
        return;
    }

    let mut raw_matches: Vec<String> = Vec::new();
    if let Err(e) = search_dir(search_path, &pattern, &mut raw_matches) {
        println!(r#"{{"ok": false, "error": "search error: {}"}}"#, e);
        return;
    }

    let truncated = raw_matches.len() >= MAX_MATCHES;
    let matches_json = raw_matches.join(", ");
    if truncated {
        println!(
            r#"{{"ok": true, "matches": [{}], "truncated": true}}"#,
            matches_json
        );
    } else {
        println!(r#"{{"ok": true, "matches": [{}]}}"#, matches_json);
    }
}
```

**关键设计决策**：
- **子串匹配而非正则**——`rustc` 编译无 `regex` crate，用 `str::contains()` 做精确子串搜索。`tool.toml` 描述中明确注明此限制
- `json_escape()` 手动处理 JSON 字符串转义——无 `serde_json`，必须自己保证 JSON 合法性
- `SKIP_DIRS` 与 Python 版对齐
- 先收集为 `Vec<String>`，最后用 `join(", ")` 组装 JSON 数组——避免手写 `[`、`]`、逗号状态机

### Rust runtime 限制说明

`rustc` 编译模式（ScriptRuntime::Rust）的固有限制：

| 限制 | 影响 | 缓解 |
|------|------|------|
| 无 `serde_json` | 需手写 JSON 解析 | `extract_str()` 覆盖扁平对象；复杂 JSON 用 Python/Bash |
| 无 `regex` | 不支持正则搜索 | `str::contains()` 做子串匹配，描述中注明 |
| 编译延迟 | 首次调用需 `rustc` 编译（~1-3s） | ScriptTool 按 mtime 缓存，后续调用直接执行 |
| 二进制体积 | 带 debug info 的 binary 约 2-5MB | 实际生产可用 `opt-level=2`（已设），binary ~300KB-1MB |

**结论**：`rustc` 模式适合性能敏感的简单数据处理。对于需要复杂 JSON 操作或正则的工具，推荐使用 Python runtime。

---

## 三种 Runtime 对比

| 维度 | Python | Bash | Rust |
|------|--------|------|------|
| JSON 支持 | 原生 (`json` 模块) | 无（python3 胶水） | 无 crate（手写提取器） |
| 文件操作 | `pathlib` 现代 API | `cat`/`mkdir`/`grep` 原生 | `std::fs` 系统 API |
| 正则搜索 | `re` 模块（PCRE） | `grep -rnI`（系统级高效） | 无（`str::contains` 子串匹配） |
| 首次延迟 | 即时 | 即时 | `rustc` 编译 ~1-3s |
| 执行性能 | 慢（解释执行） | 中等（`grep` 是 C 实现） | 快（编译优化） |
| 适合场景 | 通用工具，复杂 JSON | 文件/系统操作密集 | 计算/解析密集，简单输入 |
| 二进制文件检测 | `UnicodeDecodeError` | `grep -I` 自动跳过 | `read_to_string` 失败静默跳过 |

---

## 验证命令

```bash
# Python — 直接执行验证
echo '{"path": "/etc/hostname"}' | python3 crates/arf-mcp/tests/fixtures/read_file/main.py
echo '{"path": "/tmp/arf_test_write.txt", "content": "hello"}' | python3 crates/arf-mcp/tests/fixtures/write_file/main.py
echo '{"pattern": "fn main", "path": "crates/arf-mcp/src"}' | python3 crates/arf-mcp/tests/fixtures/search_content/main.py

# Bash — 直接执行验证
echo '{"path": "/etc/hostname"}' | bash crates/arf-mcp/tests/fixtures/read_file_bash/main.sh
echo '{"path": "/tmp/arf_test_bash.txt", "content": "hello from bash"}' | bash crates/arf-mcp/tests/fixtures/write_file_bash/main.sh
echo '{"pattern": "fn main", "path": "crates/arf-mcp/src"}' | bash crates/arf-mcp/tests/fixtures/search_content_bash/main.sh

# Rust — 先编译再测试
rustc crates/arf-mcp/tests/fixtures/read_file_rust/main.rs -o /tmp/read_file_rust && echo '{"path": "/etc/hostname"}' | /tmp/read_file_rust
rustc crates/arf-mcp/tests/fixtures/write_file_rust/main.rs -o /tmp/write_file_rust && echo '{"path": "/tmp/arf_test_rust.txt", "content": "hello from rust"}' | /tmp/write_file_rust
rustc crates/arf-mcp/tests/fixtures/search_content_rust/main.rs -o /tmp/search_content_rust && echo '{"pattern": "fn main", "path": "crates/arf-mcp/src"}' | /tmp/search_content_rust

# Rust workspace tests (确保 fixture 文件存在不破坏现有测试)
. "$HOME/.cargo/env" && cargo test --workspace
```

---

## 测试

### 角度覆盖

测试覆盖三种 runtime 的正常路径、边界条件，以及 DiscoveryModule 扫描所有 9 个 fixture 目录。

| # | 角度 | 测试内容 | Runtime |
|---|------|---------|---------|
| 1 | `[方法]` | read_file 正常读文件返回 content | Python |
| 2 | `[方法]` | read_file 正常读文件返回 content | Bash |
| 3 | `[方法]` | read_file 正常读文件返回 content | Rust |
| 4 | `[方法]` | write_file 正常写文件返回 ok + bytes | Python |
| 5 | `[方法]` | write_file 正常写文件返回 ok + bytes | Bash |
| 6 | `[方法]` | write_file 正常写文件返回 ok + bytes | Rust |
| 7 | `[方法]` | search_content 搜索返回匹配行 | Python |
| 8 | `[方法]` | search_content 搜索返回匹配行 | Bash |
| 9 | `[方法]` | search_content 搜索返回匹配行 | Rust (子串) |
| 10 | `[边界]` | read_file 文件不存在 → error | Python/Bash/Rust |
| 11 | `[边界]` | read_file 空文件 → content="" | Python/Bash/Rust |
| 12 | `[边界]` | write_file 写入空字符串 → bytes=0 | Python |
| 13 | `[边界]` | write_file 父目录不存在时自动创建 | Python |
| 14 | `[边界]` | search_content 目录不存在 → error | Python |
| 15 | `[边界]` | search_content 无匹配 → 空 matches | Python |
| 16 | `[边界]` | search_content 无效正则 → error | Python (仅 Python 支持正则验证) |
| 17 | `[类型]` | Rust search_content 不支持正则，仅子串匹配 | Rust |
| 18 | `[覆盖]` | DiscoveryModule scan fixtures 目录 → 9 个 tool | 全部 |

> 核心逻辑（文件 I/O + JSON 协议）在每个 runtime 的 `[方法]` 测试中覆盖。边界条件（空文件、不存在、父目录创建等）主要用 Python 版验证——这些测试的是工具**逻辑**而非 runtime 行为，跨 runtime 一致。

### 测试代码

#### `script_tests.rs` 扩展 — Bash runtime

```rust
// ── read_file (Bash) fixture tests ────────────────────────────────────
mod read_file_bash_fixture {
    use super::*;

    fn tool() -> ScriptTool {
        let fixture_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/read_file_bash");
        let config = ToolConfig {
            name: "read_file".into(),
            description: "Read the contents of a file (bash)".into(),
            runtime: ScriptRuntime::Bash,
            entrypoint: "main.sh".into(),
            timeout_ms: Some(10000),
            params_schema: serde_json::json!({"type": "object"}),
        };
        ScriptTool::new(config, fixture_dir)
    }

    // [方法] read_file (bash) 正常读文件返回 content
    #[tokio::test]
    async fn read_normal_file() {
        let tmp = tempfile::NamedTempFile::new().unwrap();
        fs::write(tmp.path(), "hello from bash").unwrap();
        let t = tool();
        let result = t.execute(serde_json::json!({"path": tmp.path()})).await.unwrap();
        assert_eq!(result["ok"], true);
        assert_eq!(result["content"], "hello from bash");
    }

    // [边界] read_file (bash) 文件不存在 → error
    #[tokio::test]
    async fn read_nonexistent_file() {
        let t = tool();
        let result = t.execute(serde_json::json!({"path": "/nonexistent/path.txt"})).await.unwrap();
        assert_eq!(result["ok"], false);
        assert!(result["error"].as_str().unwrap().contains("file not found"));
    }
}

// ── write_file (Bash) fixture tests ──────────────────────────────────
mod write_file_bash_fixture {
    use super::*;

    fn tool() -> ScriptTool {
        let fixture_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/write_file_bash");
        let config = ToolConfig {
            name: "write_file".into(),
            description: "Write content to a file (bash)".into(),
            runtime: ScriptRuntime::Bash,
            entrypoint: "main.sh".into(),
            timeout_ms: Some(10000),
            params_schema: serde_json::json!({"type": "object"}),
        };
        ScriptTool::new(config, fixture_dir)
    }

    // [方法] write_file (bash) 正常写文件返回 ok + bytes
    #[tokio::test]
    async fn write_normal_file() {
        let dir = tempfile::tempdir().unwrap();
        let file_path = dir.path().join("output_bash.txt");
        let t = tool();
        let result = t.execute(serde_json::json!({
            "path": file_path.to_str().unwrap(),
            "content": "Hello from bash!",
        })).await.unwrap();
        assert_eq!(result["ok"], true);
        assert!(result["bytes"].as_u64().unwrap() > 0);
        assert_eq!(fs::read_to_string(&file_path).unwrap(), "Hello from bash!");
    }
}

// ── search_content (Bash) fixture tests ──────────────────────────────
mod search_content_bash_fixture {
    use super::*;

    fn tool() -> ScriptTool {
        let fixture_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/search_content_bash");
        let config = ToolConfig {
            name: "search_content".into(),
            description: "Search for a regex pattern in files (bash)".into(),
            runtime: ScriptRuntime::Bash,
            entrypoint: "main.sh".into(),
            timeout_ms: Some(30000),
            params_schema: serde_json::json!({"type": "object"}),
        };
        ScriptTool::new(config, fixture_dir)
    }

    // [方法] search_content (bash) 搜索返回匹配行
    #[tokio::test]
    async fn search_finds_matches() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("a.rs"), "fn main() {\n    let x = 1;\n}\n").unwrap();
        fs::write(dir.path().join("b.rs"), "// no match here\n").unwrap();
        let t = tool();
        let result = t.execute(serde_json::json!({
            "pattern": "fn main",
            "path": dir.path().to_str().unwrap(),
        })).await.unwrap();
        assert_eq!(result["ok"], true);
        let matches = result["matches"].as_array().unwrap();
        assert_eq!(matches.len(), 1);
        assert!(matches[0]["content"].as_str().unwrap().contains("fn main()"));
    }
}
```

#### `script_tests.rs` 扩展 — Rust runtime

```rust
// ── read_file (Rust) fixture tests ────────────────────────────────────
mod read_file_rust_fixture {
    use super::*;

    fn tool() -> ScriptTool {
        let fixture_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/read_file_rust");
        let config = ToolConfig {
            name: "read_file".into(),
            description: "Read the contents of a file (rust)".into(),
            runtime: ScriptRuntime::Rust,
            entrypoint: "main.rs".into(),
            timeout_ms: Some(10000),
            params_schema: serde_json::json!({"type": "object"}),
        };
        ScriptTool::new(config, fixture_dir)
    }

    // [方法] read_file (rust) 正常读文件返回 content
    #[tokio::test]
    async fn read_normal_file() {
        let tmp = tempfile::NamedTempFile::new().unwrap();
        fs::write(tmp.path(), "hello from rust").unwrap();
        let t = tool();
        let result = t.execute(serde_json::json!({"path": tmp.path()})).await.unwrap();
        assert_eq!(result["ok"], true);
        assert_eq!(result["content"], "hello from rust");
    }
}

// ── write_file (Rust) fixture tests ──────────────────────────────────
mod write_file_rust_fixture {
    use super::*;

    fn tool() -> ScriptTool {
        let fixture_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/write_file_rust");
        let config = ToolConfig {
            name: "write_file".into(),
            description: "Write content to a file (rust)".into(),
            runtime: ScriptRuntime::Rust,
            entrypoint: "main.rs".into(),
            timeout_ms: Some(10000),
            params_schema: serde_json::json!({"type": "object"}),
        };
        ScriptTool::new(config, fixture_dir)
    }

    // [方法] write_file (rust) 正常写文件返回 ok + bytes
    #[tokio::test]
    async fn write_normal_file() {
        let dir = tempfile::tempdir().unwrap();
        let file_path = dir.path().join("output_rust.txt");
        let t = tool();
        let result = t.execute(serde_json::json!({
            "path": file_path.to_str().unwrap(),
            "content": "Hello from rust!",
        })).await.unwrap();
        assert_eq!(result["ok"], true);
        assert!(result["bytes"].as_u64().unwrap() > 0);
        assert_eq!(fs::read_to_string(&file_path).unwrap(), "Hello from rust!");
    }
}

// ── search_content (Rust) fixture tests ──────────────────────────────
mod search_content_rust_fixture {
    use super::*;

    fn tool() -> ScriptTool {
        let fixture_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/search_content_rust");
        let config = ToolConfig {
            name: "search_content".into(),
            description: "Search for a substring in files (rust)".into(),
            runtime: ScriptRuntime::Rust,
            entrypoint: "main.rs".into(),
            timeout_ms: Some(30000),
            params_schema: serde_json::json!({"type": "object"}),
        };
        ScriptTool::new(config, fixture_dir)
    }

    // [方法] search_content (rust) 子串匹配返回匹配行
    #[tokio::test]
    async fn search_finds_matches() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("a.rs"), "fn main() {\n    let x = 1;\n}\n").unwrap();
        fs::write(dir.path().join("b.rs"), "// no match here\n").unwrap();
        let t = tool();
        let result = t.execute(serde_json::json!({
            "pattern": "fn main",
            "path": dir.path().to_str().unwrap(),
        })).await.unwrap();
        assert_eq!(result["ok"], true);
        let matches = result["matches"].as_array().unwrap();
        assert_eq!(matches.len(), 1);
        assert!(matches[0]["content"].as_str().unwrap().contains("fn main()"));
    }

    // [类型] Rust 版不支持正则，仅做子串匹配 — "[" 会被当作普通字符
    #[tokio::test]
    async fn search_substring_not_regex() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("test.rs"), "[unclosed bracket\n").unwrap();
        let t = tool();
        // Rust 版用 str::contains，不会报 invalid regex
        let result = t.execute(serde_json::json!({
            "pattern": "[unclosed",
            "path": dir.path().to_str().unwrap(),
        })).await.unwrap();
        assert_eq!(result["ok"], true);
        let matches = result["matches"].as_array().unwrap();
        assert_eq!(matches.len(), 1); // Rust 当作子串匹配，找到了
    }
}
```

#### `discovery_tests.rs` 扩展测试

```rust
// [覆盖] DiscoveryModule scan fixtures 目录 → 9 个 tool (3 tools × 3 runtimes)
#[tokio::test]
async fn scan_fixtures_discovers_all_nine_tools() {
    let fixture_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures");
    let mut discovery = DiscoveryModule::new(fixture_root.clone());
    discovery.scan().await.unwrap();

    let tools = discovery.list_tools();
    assert_eq!(tools.len(), 9, "fixtures should have exactly 9 tools (3 × 3 runtimes)");

    let names: Vec<&str> = tools.iter().map(|t| t.name()).collect();
    assert_eq!(names.iter().filter(|n| **n == "read_file").count(), 3);
    assert_eq!(names.iter().filter(|n| **n == "write_file").count(), 3);
    assert_eq!(names.iter().filter(|n| **n == "search_content").count(), 3);
}
```
