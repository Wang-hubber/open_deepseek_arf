use std::fs;
use std::io::{self, Read};
use std::path::Path;

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
    let content = extract_str(input, "content").unwrap_or_default();

    if let Some(parent) = Path::new(&path_str).parent() {
        if !parent.as_os_str().is_empty() {
            if let Err(e) = fs::create_dir_all(parent) {
                println!(r#"{{"ok": false, "error": "mkdir error: {}"}}"#, e);
                return;
            }
        }
    }

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
