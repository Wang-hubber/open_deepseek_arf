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

```
crates/arf-mcp/tests/fixtures/
├── read_file/
│   ├── tool.toml
│   └── main.py
├── write_file/
│   ├── tool.toml
│   └── main.py
└── search_content/
    ├── tool.toml
    └── main.py
```

| 文件 | 操作 | 内容 |
|------|------|------|
| `crates/arf-mcp/tests/fixtures/read_file/tool.toml` | 新建 | read_file 元数据 |
| `crates/arf-mcp/tests/fixtures/read_file/main.py` | 新建 | 读取文件内容脚本 |
| `crates/arf-mcp/tests/fixtures/write_file/tool.toml` | 新建 | write_file 元数据 |
| `crates/arf-mcp/tests/fixtures/write_file/main.py` | 新建 | 写入文件内容脚本 |
| `crates/arf-mcp/tests/fixtures/search_content/tool.toml` | 新建 | search_content 元数据 |
| `crates/arf-mcp/tests/fixtures/search_content/main.py` | 新建 | 正则搜索文件内容脚本 |

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

## 验证命令

```bash
# 验证 Python 脚本语法和 stdin/stdout 协议
echo '{"path": "/etc/hostname"}' | python3 crates/arf-mcp/tests/fixtures/read_file/main.py
echo '{"path": "/tmp/arf_test_write.txt", "content": "hello"}' | python3 crates/arf-mcp/tests/fixtures/write_file/main.py
echo '{"pattern": "fn main", "path": "crates/arf-mcp/src"}' | python3 crates/arf-mcp/tests/fixtures/search_content/main.py

# Rust workspace tests (确保 fixture 文件存在不破坏现有测试)
. "$HOME/.cargo/env" && cargo test --workspace

# DiscoveryModule 集成验证（fixture 目录作为 MCP root）
. "$HOME/.cargo/env" && cargo test -p arf-mcp --test integration discovery
```

---

## 测试

### 角度覆盖

| # | 角度 | 测试内容 | 文件 |
|---|------|---------|------|
| 1 | `[构造]` | tool.toml 解析成功，字段完整 | `config_tests.rs` (扩展) |
| 2 | `[方法]` | read_file 正常读文件返回 content | `script_tests.rs` (扩展) |
| 3 | `[方法]` | write_file 正常写文件返回 ok + bytes | `script_tests.rs` (扩展) |
| 4 | `[方法]` | search_content 搜索返回匹配行 | `script_tests.rs` (扩展) |
| 5 | `[边界]` | read_file 文件不存在 → error | `script_tests.rs` (扩展) |
| 6 | `[边界]` | read_file 空文件 → content="" | `script_tests.rs` (扩展) |
| 7 | `[边界]` | write_file 写入空字符串 → bytes=0 | `script_tests.rs` (扩展) |
| 8 | `[边界]` | search_content 目录不存在 → error | `script_tests.rs` (扩展) |
| 9 | `[边界]` | search_content 无效正则 → error | `script_tests.rs` (扩展) |
| 10 | `[边界]` | search_content 无匹配 → 空 matches | `script_tests.rs` (扩展) |
| 11 | `[边界]` | search_content 超过 100 匹配 → truncated | `script_tests.rs` (扩展) |
| 12 | `[边界]` | read_file 二进制文件 → error | `script_tests.rs` (扩展) |
| 13 | `[边界]` | write_file 父目录不存在时自动创建 | `script_tests.rs` (扩展) |
| 14 | `[覆盖]` | DiscoveryModule scan fixtures 目录 → 3 个 tool | `discovery_tests.rs` (扩展) |

### 测试代码

所有测试使用 Rust `#[test]`，通过 temp dir 创建测试文件后调用 `ScriptTool::execute()`。不需要创建额外的 Python 脚本——直接用 fixture 目录下的 `main.py`。

#### `script_tests.rs` 扩展测试

```rust
// ── read_file fixture tests ──────────────────────────────────────────
mod read_file_fixture {
    use super::*;

    fn tool() -> ScriptTool {
        let fixture_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/read_file");
        let config = ToolConfig {
            name: "read_file".into(),
            description: "Read the contents of a file".into(),
            runtime: ScriptRuntime::Python,
            entrypoint: "main.py".into(),
            timeout_ms: Some(10000),
            params_schema: serde_json::json!({"type": "object"}),
        };
        ScriptTool::new(config, fixture_dir)
    }

    // [方法] read_file 正常读文件返回 content
    #[tokio::test]
    async fn read_normal_file() {
        let tmp = tempfile::NamedTempFile::new().unwrap();
        fs::write(tmp.path(), "hello world").unwrap();

        let t = tool();
        let result = t.execute(serde_json::json!({"path": tmp.path()})).await.unwrap();
        assert_eq!(result["ok"], true);
        assert_eq!(result["content"], "hello world");
        assert_eq!(result["path"], tmp.path().to_str().unwrap());
    }

    // [边界] read_file 文件不存在 → error
    #[tokio::test]
    async fn read_nonexistent_file() {
        let t = tool();
        let result = t.execute(serde_json::json!({"path": "/nonexistent/path.txt"})).await.unwrap();
        assert_eq!(result["ok"], false);
        assert!(result["error"].as_str().unwrap().contains("file not found"));
    }

    // [边界] read_file 空文件 → content=""
    #[tokio::test]
    async fn read_empty_file() {
        let tmp = tempfile::NamedTempFile::new().unwrap();
        let t = tool();
        let result = t.execute(serde_json::json!({"path": tmp.path()})).await.unwrap();
        assert_eq!(result["ok"], true);
        assert_eq!(result["content"], "");
    }
}

// ── write_file fixture tests ─────────────────────────────────────────
mod write_file_fixture {
    use super::*;

    fn tool() -> ScriptTool {
        let fixture_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/write_file");
        let config = ToolConfig {
            name: "write_file".into(),
            description: "Write content to a file".into(),
            runtime: ScriptRuntime::Python,
            entrypoint: "main.py".into(),
            timeout_ms: Some(10000),
            params_schema: serde_json::json!({"type": "object"}),
        };
        ScriptTool::new(config, fixture_dir)
    }

    // [方法] write_file 正常写文件返回 ok + bytes
    #[tokio::test]
    async fn write_normal_file() {
        let dir = tempfile::tempdir().unwrap();
        let file_path = dir.path().join("output.txt");

        let t = tool();
        let result = t.execute(serde_json::json!({
            "path": file_path.to_str().unwrap(),
            "content": "Hello, 世界!",
        })).await.unwrap();

        assert_eq!(result["ok"], true);
        assert_eq!(result["path"], file_path.to_str().unwrap());
        assert!(result["bytes"].as_u64().unwrap() > 0);

        let written = fs::read_to_string(&file_path).unwrap();
        assert_eq!(written, "Hello, 世界!");
    }

    // [边界] write_file 写入空字符串 → bytes=0
    #[tokio::test]
    async fn write_empty_string() {
        let dir = tempfile::tempdir().unwrap();
        let file_path = dir.path().join("empty.txt");

        let t = tool();
        let result = t.execute(serde_json::json!({
            "path": file_path.to_str().unwrap(),
            "content": "",
        })).await.unwrap();

        assert_eq!(result["ok"], true);
        assert_eq!(result["bytes"], 0);

        let written = fs::read_to_string(&file_path).unwrap();
        assert_eq!(written, "");
    }

    // [边界] write_file 父目录不存在时自动创建
    #[tokio::test]
    async fn write_creates_parent_dirs() {
        let dir = tempfile::tempdir().unwrap();
        let file_path = dir.path().join("a/b/c/output.txt");

        let t = tool();
        let result = t.execute(serde_json::json!({
            "path": file_path.to_str().unwrap(),
            "content": "deep",
        })).await.unwrap();

        assert_eq!(result["ok"], true);
        assert!(file_path.exists());
        assert_eq!(fs::read_to_string(&file_path).unwrap(), "deep");
    }
}

// ── search_content fixture tests ─────────────────────────────────────
mod search_content_fixture {
    use super::*;

    fn tool() -> ScriptTool {
        let fixture_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/search_content");
        let config = ToolConfig {
            name: "search_content".into(),
            description: "Search for a regex pattern in files".into(),
            runtime: ScriptRuntime::Python,
            entrypoint: "main.py".into(),
            timeout_ms: Some(30000),
            params_schema: serde_json::json!({"type": "object"}),
        };
        ScriptTool::new(config, fixture_dir)
    }

    // [方法] search_content 搜索返回匹配行
    #[tokio::test]
    async fn search_finds_matches() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("a.txt"), "fn main() {\n    let x = 1;\n}\n").unwrap();
        fs::write(dir.path().join("b.txt"), "// no match here\n").unwrap();

        let t = tool();
        let result = t.execute(serde_json::json!({
            "pattern": "fn main",
            "path": dir.path().to_str().unwrap(),
        })).await.unwrap();

        assert_eq!(result["ok"], true);
        let matches = result["matches"].as_array().unwrap();
        assert_eq!(matches.len(), 1);
        assert_eq!(matches[0]["file"].as_str().unwrap(), dir.path().join("a.txt").to_str().unwrap());
        assert_eq!(matches[0]["line"], 1);
        assert!(matches[0]["content"].as_str().unwrap().contains("fn main()"));
    }

    // [边界] search_content 目录不存在 → error
    #[tokio::test]
    async fn search_nonexistent_dir() {
        let t = tool();
        let result = t.execute(serde_json::json!({
            "pattern": "test",
            "path": "/nonexistent/dir",
        })).await.unwrap();
        assert_eq!(result["ok"], false);
        assert!(result["error"].as_str().unwrap().contains("directory not found"));
    }

    // [边界] search_content 无效正则 → error
    #[tokio::test]
    async fn search_invalid_regex() {
        let dir = tempfile::tempdir().unwrap();
        let t = tool();
        let result = t.execute(serde_json::json!({
            "pattern": "[unclosed",
            "path": dir.path().to_str().unwrap(),
        })).await.unwrap();
        assert_eq!(result["ok"], false);
        assert!(result["error"].as_str().unwrap().contains("invalid regex"));
    }

    // [边界] search_content 无匹配 → 空 matches
    #[tokio::test]
    async fn search_no_matches() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("only.txt"), "nothing here\n").unwrap();

        let t = tool();
        let result = t.execute(serde_json::json!({
            "pattern": "ZZZZZ",
            "path": dir.path().to_str().unwrap(),
        })).await.unwrap();

        assert_eq!(result["ok"], true);
        let matches = result["matches"].as_array().unwrap();
        assert_eq!(matches.len(), 0);
    }
}
```

#### `discovery_tests.rs` 扩展测试

```rust
// [覆盖] DiscoveryModule scan fixtures 目录 → 3 个 tool
#[tokio::test]
async fn scan_fixtures_discovers_all_three_tools() {
    let fixture_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures");
    let mut discovery = DiscoveryModule::new(fixture_root.clone());
    discovery.scan().await.unwrap();

    let tools = discovery.list_tools();
    assert_eq!(tools.len(), 3, "fixtures should have exactly 3 tools");

    let names: Vec<&str> = tools.iter().map(|t| t.name()).collect();
    assert!(names.contains(&"read_file"));
    assert!(names.contains(&"write_file"));
    assert!(names.contains(&"search_content"));
}
```
