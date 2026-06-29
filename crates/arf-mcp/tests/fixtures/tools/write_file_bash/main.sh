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

# Write content (printf is safe for special chars unlike echo)
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
