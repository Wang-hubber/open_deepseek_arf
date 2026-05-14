#!/bin/bash
set -e

WORKSPACE_DIR="${ARF_WORKSPACE:-/app/workspace}"

# Create workspace if it doesn't exist
if [ ! -f "$WORKSPACE_DIR/arf_agent.yaml" ]; then
    echo "Initializing ARF workspace at $WORKSPACE_DIR ..."
    mkdir -p "$WORKSPACE_DIR"/{models,tools,skills,memory}
    cat > "$WORKSPACE_DIR/arf_agent.yaml" << 'YAML'
# ARF Workspace Configuration
# This file persists across conversations -- user profile, preferences,
# and resource configurations.

name: default
YAML
    echo "Workspace created."
fi

echo "Starting ARF server on 0.0.0.0:8000 ..."
exec arf web --workspace "$WORKSPACE_DIR" --host 0.0.0.0 --port 8000
