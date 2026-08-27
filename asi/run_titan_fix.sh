#!/bin/bash
# Resilient wrapper: retries the given asi.py invocation on crash (transient
# Titan/Slurm submission errors happened historically), relying on
# search_state.json to resume mid-run rather than restart from scratch.
# Usage: run_titan_fix.sh <label> <max_retries> -- <asi.py args...>
set -u
label="$1"; shift
max_retries="$1"; shift
if [ "$1" != "--" ]; then echo "expected --"; exit 1; fi
shift

cd /home/jaco/school/stage/asi
attempt=1
while [ "$attempt" -le "$max_retries" ]; do
  echo "=== [$label] attempt $attempt/$max_retries starting $(date -Iseconds) ==="
  ./asi.py "$@"
  status=$?
  if [ "$status" -eq 0 ]; then
    echo "=== [$label] SUCCEEDED on attempt $attempt at $(date -Iseconds) ==="
    exit 0
  fi
  echo "=== [$label] attempt $attempt FAILED (exit $status) at $(date -Iseconds), retrying in 60s ==="
  attempt=$((attempt + 1))
  sleep 60
done
echo "=== [$label] EXHAUSTED $max_retries attempts, giving up ==="
exit 1
