#!/bin/bash
# session_start hook example — log session starts to a timestamped file.

logfile="${ARF_WORKSPACE:-./memory}/session.log"
echo "[$(date -Iseconds)] session started" >> "$logfile"
