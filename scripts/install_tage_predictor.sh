#!/usr/bin/env bash
# Drops the TAGE branch predictor (asi/libs/TAGE submodule) into the snipersim
# submodule's build tree and registers it in the predictor factory.
#
# This only touches snipersim's working tree (never committed/pushed there);
# re-running it is safe after a fresh `git submodule update`.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAGE_SRC="$REPO_ROOT/asi/libs/TAGE"
BP_DIR="$REPO_ROOT/snipersim/common/performance_model/branch_predictors"
FACTORY_CC="$REPO_ROOT/snipersim/common/performance_model/branch_predictor.cc"

for f in tage_branch_predictor.h tage_branch_predictor.cc tage_base_predictor.h; do
    cp "$TAGE_SRC/$f" "$BP_DIR/$f"
done

python3 - "$FACTORY_CC" <<'PYEOF'
import re, sys

path = sys.argv[1]
text = open(path).read()

if '"tage_branch_predictor.h"' not in text:
    text = text.replace(
        '#include "nn_branch_predictor.h"\n',
        '#include "nn_branch_predictor.h"\n#include "tage_branch_predictor.h"\n',
    )

if 'type == "tage"' not in text:
    marker = (
        '      else if (type == "nn") {\n'
        '          UInt32 batch_length = cfg->getIntArray("perf_model/branch_predictor/batch_length", core_id);\n'
        '          double learning_rate = cfg->getFloatArray("perf_model/branch_predictor/learning_rate", core_id);\n'
        '          return new NNBranchPredictor("branch_predictor", core_id, batch_length, learning_rate);\n'
        '      }\n'
    )
    replacement = marker + (
        '      else if (type == "tage")\n'
        '      {\n'
        '         return new TageBranchPredictor("branch_predictor", core_id);\n'
        '      }\n'
    )
    assert marker in text, "expected nn_branch_predictor case not found; branch_predictor.cc may have changed upstream"
    text = text.replace(marker, replacement)

open(path, "w").write(text)
PYEOF

echo "TAGE predictor installed into snipersim. Add to a config with:"
echo "  [perf_model/branch_predictor]"
echo "  type = tage"
echo "  mispredict_penalty = 8"
