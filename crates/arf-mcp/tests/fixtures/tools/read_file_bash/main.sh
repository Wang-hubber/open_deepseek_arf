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

# Use python3 for JSON encoding (guarantees correct escaping of special chars)
python3 -c "
import json
content = open('$PATH_VAL').read()
print(json.dumps({'ok': True, 'content': content, 'path': '$PATH_VAL'}))
"
