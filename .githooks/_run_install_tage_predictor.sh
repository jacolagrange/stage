#!/usr/bin/env bash
# Shared by post-checkout/post-merge: re-installs the TAGE predictor into
# snipersim whenever it and the TAGE submodule are both present.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"

if [ -f "$REPO_ROOT/asi/libs/TAGE/tage_branch_predictor.cc" ] \
   && [ -d "$REPO_ROOT/snipersim/common/performance_model/branch_predictors" ]; then
    "$REPO_ROOT/scripts/install_tage_predictor.sh"
fi
