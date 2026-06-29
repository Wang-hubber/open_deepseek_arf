use std::fs;
use std::io::{self, Read};
use std::path::Path;

const SKIP_DIRS: &[&str] = &[
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "target", ".mypy_cache", ".pytest_cache",
];
const MAX_MATCHES: usize = 100;

fn extract_str(json: &str, key: &str) -> Option<String> {
    let search = format!("\"{}\"", key);
    let pos = json.find(&search)?;
    let after_key = &json[pos + search.len()..];
    let colon = after_key.find(':')?;
    let after_colon = &after_key[colon + 1..];
    let trimmed = after_colon.trim_start();
    if !trimmed.starts_with('"') {
        return None;
    }
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
                Some(c) => {
                    result.push('\\');
                    result.push(c);
                }
                None => return None,
            },
            Some('"') => break,
            Some(c) => result.push(c),
        }
    }
    Some(result)
}

fn json_escape(s: &str) -> String {
    s.replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('\n', "\\n")
        .replace('\r', "\\r")
        .replace('\t', "\\t")
}

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
