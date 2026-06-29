use std::fs;
use std::io::{self, Read};

/// Extract the value of a string field from flat JSON like {"key": "value"}.
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

    match fs::read_to_string(&path_str) {
        Ok(content) => {
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
            println!(r#"{{"ok": false, "error": "read error: {}"}}"#, e);
        }
    };
}
