"""edit_file tool — pattern-based file editing with unified diff output."""

import difflib
import os
import tempfile


def _normalize_line_endings(text: str) -> str:
    """Replace Windows CRLF with Unix LF."""
    return text.replace("\r\n", "\n")


def _create_unified_diff(original: str, modified: str, filepath: str) -> str:
    """Generate unified diff between original and modified content."""
    diff_lines = list(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            modified.splitlines(keepends=True),
            fromfile=filepath,
            tofile=filepath,
        )
    )
    return "".join(diff_lines)


def _wrap_in_fence(diff_text: str) -> str:
    """Wrap diff text in a variable-sized backtick fence.

    Chooses a fence size at least 3 that does not conflict with
    any run of consecutive backticks inside the diff content.
    """
    max_run = 0
    current_run = 0
    for ch in diff_text:
        if ch == "`":
            current_run += 1
            max_run = max(max_run, current_run)
        else:
            current_run = 0

    fence_size = max(max_run + 1, 3)
    fence = "`" * fence_size
    return f"{fence}\n{diff_text}\n{fence}"


def _try_whitespace_flexible_match(
    content: str, old_text: str, new_text: str
) -> str | None:
    """Try to match *old_text* via line-level whitespace-flexible comparison.

    Each line is compared by ``.strip()``.  When all lines match, the
    indentation of the first matched line is carried over to the replacement.

    Returns the updated content on success, or ``None`` when no match is found.
    """
    old_lines = old_text.split("\n")
    content_lines = content.split("\n")
    new_text_lines = new_text.split("\n")

    # Determine the indentation of the first replacement line so we can
    # compute per-line relative indent adjustments.
    if new_text_lines:
        new_first_line = new_text_lines[0]
        new_first_indent_len = len(new_first_line) - len(new_first_line.lstrip())
    else:
        new_first_indent_len = 0

    for i in range(len(content_lines) - len(old_lines) + 1):
        # Check whether all lines match (whitespace-insensitive)
        match = True
        for j in range(len(old_lines)):
            if old_lines[j].strip() != content_lines[i + j].strip():
                match = False
                break

        if not match:
            continue

        # Capture original indentation of the first matched line
        first_content_line = content_lines[i]
        base_indent = first_content_line[
            : len(first_content_line) - len(first_content_line.lstrip())
        ]

        # Build replacement lines with preserved indentation
        replacement: list[str] = []
        for nl in new_text_lines:
            stripped = nl.lstrip()
            current_indent_len = len(nl) - len(stripped)
            relative_indent = " " * max(0, current_indent_len - new_first_indent_len)
            replacement.append(base_indent + relative_indent + stripped)

        content_lines[i : i + len(old_lines)] = replacement
        return "\n".join(content_lines)

    return None


async def execute(
    path: str, edits: list[dict], dryRun: bool = False,
    encoding: str = "utf-8", **kwargs
) -> dict:
    """Apply one or more text edits to a file and return a unified diff.

    Parameters
    ----------
    path :
        File to edit.
    edits :
        Sequence of ``{"oldText": …, "newText": …}`` edit operations.
    dryRun :
        When ``True`` the diff is returned but the file is not written.

    Returns
    -------
    dict
        ``{"ok": True, "diff": …, "dry_run": …, "applied": …}`` on success,
        or ``{"ok": False, "error": "…"}`` on failure.
    """
    if not os.path.isfile(path):
        return {"ok": False, "error": f"Not a file: {path}"}

    try:
        with open(path, "r", encoding=encoding) as f:
            original_content = f.read()
    except (UnicodeDecodeError, LookupError) as e:
        return {
            "ok": False,
            "error": f"Cannot read {path} as {encoding} text — {e}",
        }
    except OSError as e:
        return {"ok": False, "error": str(e)}

    # Reject files that contain null bytes (strong indicator of binary content)
    if "\x00" in original_content:
        return {
            "ok": False,
            "error": f"Cannot edit {path} — file appears to be binary",
        }

    # Normalise line endings (CRLF -> LF) for consistent matching
    original_content = _normalize_line_endings(original_content)
    modified_content = original_content

    for edit in edits:
        old_text = _normalize_line_endings(edit["oldText"])
        new_text = _normalize_line_endings(edit["newText"])

        if not old_text:
            continue

        # 1) Exact substring match
        if old_text in modified_content:
            modified_content = modified_content.replace(old_text, new_text, 1)
            continue

        # 2) Whitespace-flexible line-level match
        result = _try_whitespace_flexible_match(modified_content, old_text, new_text)
        if result is not None:
            modified_content = result
        else:
            return {"ok": False, "error": f"No match found for text: {old_text!r}"}

    # Build the unified diff
    diff_text = _create_unified_diff(original_content, modified_content, path)
    diff_text = _wrap_in_fence(diff_text)

    # Persist (unless dry-run)
    if not dryRun:
        try:
            tmpdir = os.path.dirname(path) or "."
            fd, tmpname = tempfile.mkstemp(dir=tmpdir, prefix=".edit_", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding=encoding) as f:
                    f.write(modified_content)
                os.replace(tmpname, path)
            except Exception:
                try:
                    os.unlink(tmpname)
                except OSError:
                    pass
                raise
        except OSError as e:
            return {"ok": False, "error": str(e)}

    return {
        "ok": True,
        "diff": diff_text,
        "dry_run": dryRun,
        "applied": not dryRun,
    }
