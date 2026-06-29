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
# || true: grep returns exit code 1 for no matches
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
python3 -c "
import json, sys

lines = sys.argv[1].split('\n')
max_matches = 100
matches = []
for line in lines:
    if not line.strip():
        continue
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
