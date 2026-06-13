# Filesystem Plugin 工具参考

> ARF 内置文件系统操作插件，完全对齐 MCP `@modelcontextprotocol/server-filesystem` v0.2.0 API。
> 纯 Python 实现，零外部依赖。安全由框架层 `PathCheckToolGuard` 统一拦截。

## 工具清单

### 读取

| 工具名 | description | 功能简述 |
|--------|---------|----------|
| `read_text_file` | Read the complete contents of a file as UTF-8 text. Use the `head` parameter to read only the first N lines, or the `tail` parameter to read only the last N lines. Cannot specify both simultaneously. | 以 UTF-8 编码读取文本文件全部内容。支持 `head`（前 N 行）和 `tail`（后 N 行）参数，二者互斥。带行号输出。 |
| `read_media_file` | Read an image or audio file. Returns the base64 encoded data and MIME type. Supports PNG, JPG, GIF, WebP, BMP, SVG (images) and MP3, WAV, OGG, FLAC (audio). | 读取图片或音频文件，返回 base64 编码数据和 MIME 类型。支持常见图片格式（PNG/JPG/GIF/WebP/BMP/SVG）和音频格式（MP3/WAV/OGG/FLAC）。最大 10 MB。 |
| `read_multiple_files` | Read the contents of multiple files simultaneously. Each file's content is returned with its path as reference. Failed reads for individual files won't stop the entire operation. | 并行批量读取多个文件，返回每个文件的路径和内容（或错误信息）。单个文件读取失败不影响其他文件。适合一次查看/对比多个文件。 |

### 列表与搜索

| 工具名 | description | 功能简述 |
|--------|---------|----------|
| `list_directory` | Get a listing of all files and directories in a specified path. Results distinguish between files and directories with `[FILE]` and `[DIR]` prefixes. | 列出目录内容，以 `[FILE]` 和 `[DIR]` 前缀区分文件和目录。简洁纯文本输出。 |
| `list_directory_with_sizes` | Get a detailed listing including file sizes. Results distinguish between files and directories with `[FILE]` and `[DIR]` prefixes. Includes summary with total files, directories, and combined size. | 列出目录内容并显示文件大小（人类可读格式）。支持按名称或大小排序。末尾输出统计摘要（文件数、目录数、总大小）。 |
| `directory_tree` | Get a recursive tree view of files and directories as a JSON structure. Each entry includes name, type (file/directory), and children for directories. Files have no children array, directories always have a children array (may be empty). | 递归生成目录树，返回 JSON 结构。每个节点包含 name、type（`"file"` / `"directory"`）和可选的 children 数组。目录始终有 children（可能为空），文件不含 children。支持 excludePatterns 过滤。 |
| `search_files` | Recursively search for files and directories matching a glob pattern. Use patterns like `*.ext` to match files in current directory, and `**/*.ext` to match files in all subdirectories. Returns full paths to all matching items. | 递归搜索匹配 glob 模式的文件和目录。`*.py` 只匹配当前目录，`**/*.py` 匹配所有子目录。自动排除 `.git`、`__pycache__`、`node_modules` 等常见忽略目录。最多返回 500 条结果。 |

### 信息

| 工具名 | description | 功能简述 |
|--------|---------|----------|
| `get_file_info` | Retrieve detailed metadata about a file or directory. Returns size, creation time, last modified time, last accessed time, type, and permissions. | 获取文件/目录的详细元数据：大小、创建时间、修改时间、访问时间、类型（文件/目录）、权限（八进制）。 |
| `list_allowed_directories` | Returns the list of directories that this server is allowed to access. Use this to understand which directories are available before trying to access files. | 返回当前可访问的目录列表。用于在操作文件前了解访问范围边界。 |

### 写入

| 工具名 | description | 功能简述 |
|--------|---------|----------|
| `write_file` | Create a new file or completely overwrite an existing file with new content. Handles text content with proper UTF-8 encoding. | 创建新文件或覆写已有文件。自动创建不存在的父目录。使用原子写入（临时文件 + rename），防止并发写入损坏。⚠️ 破坏性操作。 |
| `edit_file` | Make selective line-based edits to a text file. Each edit replaces exact text sequences with new content. Returns a git-style diff showing the changes made. Use `dryRun=true` to preview changes without applying them. | 对文本文件进行选择性编辑。支持精确文本匹配 + 空白灵活回退匹配，自动保留原始缩进。返回 unified diff 格式的变更预览。`dryRun=true` 仅预览不写入。使用原子写入。⚠️ 破坏性操作，建议先 dryRun。 |

### 管理

| 工具名 | description | 功能简述 |
|--------|---------|----------|
| `create_directory` | Create a new directory or ensure a directory exists. Can create multiple nested directories in one operation. If the directory already exists, this operation will succeed silently. | 递归创建目录，自动创建所有不存在的父目录。目录已存在时静默成功（幂等）。 |
| `move_file` | Move or rename files and directories. Can move files between directories and rename them in a single operation. Fails if the destination exists. | 移动或重命名文件/目录。跨目录移动和重命名可在一次操作中完成。目标路径已存在时操作失败。 |
| `delete_file` | Delete a file or directory. Use `recursive=true` to delete non-empty directories. Exercise caution — deletions are permanent. | 删除文件或目录。设置 `recursive=true` 可递归删除非空目录。⚠️ 删除操作不可逆。 |

## 使用约定

所有工具通过框架 MCP 层注册，命名空间前缀为 `filesystem__`（例如 `filesystem__read_text_file`）。Agent 可通过 `plugins: [filesystem]` 在配置中启用。

AI 将在系统提示中收到 `filesystem` 技能引导，指导其优先使用这些工具而非 shell 命令。

## 安全模型

工具层不做路径校验，所有路径合法性由框架 `PathCheckToolGuard`（`pre_action` 阻塞钩子）统一拦截：
- 路径必须在配置的允许目录范围内
- `..` 穿越攻击被拦截
- 符号链接目标验证
