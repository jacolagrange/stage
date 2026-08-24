"""
Parameter-importance pre-screen, run once before any search strategy (see
cli.py's --preeval-samples/--preeval-method flags). Prunes every parameter
whose estimated importance falls below a threshold down to its default-only
value -- so greedy/spea2 (which both read the module-level PARAM_SPACE in
their own modules, see config.active_params's docstring) never spend
evaluations varying it.

Two screening methods are available (--preeval-method):

  perceptron (default) -- randomly samples a handful of full configurations,
    trains a single linear unit to predict how far each sample lands from the
    baseline in (ASI, speedup) space from its parameters' one-hot encoding.
    This is a rough screen, not a substitute for the strategies' own on-line
    sensitivity logic (greedy's freezing, spea2's full-space search): with
    only a few dozen random samples across ~20 parameters, a conditional
    parameter that's only active under a rarely-sampled branch_predictor_type
    (e.g. nn_learning_rate under "nn") can easily look unimportant by chance
    and get pruned. Keep --preeval-samples comfortably larger than the
    parameter count, or skip screening for runs where every parameter
    matters.

  plackett_burman -- follows Yi, Lilja & Hawkins, "A Statistically Rigorous
    Approach for Improving Simulation Methodology" (Section 2): every
    always-relevant, non-branch-predictor parameter is treated as a two-level
    factor -- tested at the minimum and maximum of its listed values, the two
    extremes of its declared range, matching the paper's "just outside the
    normal range" guidance -- and varied according to a Plackett-Burman
    design with foldover, deliberately oversized (see _next_pb_size) to leave
    a couple of spare "dummy" columns tied to no real parameter. A
    parameter's effect is computed *separately per objective* (ASI, speedup)
    as the sum, over every run, of its assigned +-1 level times that run's
    *signed* deviation from baseline on that objective; a dummy column's
    effect is computed the same way despite representing nothing, so its
    magnitude is pure per-objective noise. Effects are kept per-objective
    (not combined into one non-negative Euclidean distance) because
    combining them discards direction and lets a single dominant/saturating
    parameter (e.g. a crippling rob_window_size=16) swamp every column's
    effect sum at once -- both real and dummy alike -- which in practice
    produced an unstable all-kept-or-all-pruned split instead of a real
    ranking. Following the paper's own Table 6 (whose "Dummy Parameter" rows
    are the yardstick for which real parameters' ranks are actually
    trustworthy), a real parameter is kept if either objective's |effect|
    exceeds that objective's own largest |dummy effect| -- not an arbitrary
    fraction of the largest real effect.

    branch_predictor_type itself, and its own predictor-specific knobs (see
    BRANCH_PREDICTOR_PARAMS), are excluded from that design entirely -- a
    >2-valued categorical choice like the predictor type doesn't fit a
    strictly 2-level PB factor, and forcing it into one would mean two of its
    four values (e.g. pentium_m, tage) are never tested at all. Rather than
    screen them some other way, this method simply spends no PB evaluations
    on them -- but leaves them untouched (their full PARAM_SPACE range, not
    pruned) in the returned param space, so the search strategy that runs
    afterward is still free to explore branch_predictor_type normally.

Both methods are rough screens, not substitutes for the strategies' own
on-line sensitivity logic.

A completed screen is cached to outputdir/preeval/screen_cache.json and
reused automatically the next time screen_param_space() is called with a
matching config/benchmarks/alpha/method (and, for perceptron, matching
num_samples/keep_threshold/seed too, since unlike plackett_burman its result
actually depends on them) -- see cli.py's --preeval-cache flag to reuse that
cache on a run that doesn't pass --preeval-samples/--preeval-method at all.
An in-progress screen has no partial checkpoint though: interrupting one
before it completes still means rerunning the whole thing from scratch.
"""
import json
import random
from pathlib import Path
from typing import Any

from .config import PARAM_SPACE, DEFAULTS, BRANCH_PREDICTOR_PARAMS, CONDITIONAL_PARAMS
from .models import DesignPoint
from .greedy import evaluate_point, params_key, print_evaluated_point, compute_baseline
from .state import cleanup_dirs, write_json_atomic, point_to_dict, point_from_dict


def _random_entity(rng: random.Random, param_space: dict[str, list]) -> dict[str, Any]:
    """A fully-specified configuration, mirroring spea2._random_entity: one
    value per always-relevant parameter, plus the chosen branch predictor
    type's own knobs."""
    entity = {
        param: rng.choice(values)
        for param, values in param_space.items()
        if param not in CONDITIONAL_PARAMS
    }
    for param in BRANCH_PREDICTOR_PARAMS.get(entity["branch_predictor_type"], ()):
        entity[param] = rng.choice(param_space[param])
    return entity


def _encode_features(params: dict[str, Any], param_space: dict[str, list]) -> dict[str, float]:
    """One binary feature per (param, non-default value) pair -- 1.0 if this
    point uses that value, else 0.0. A missing key (conditional param not
    active for this point's predictor type) is treated as "at default", same
    as the rest of the codebase's sparse-dict convention."""
    features = {}
    for param, values in param_space.items():
        default = values[0]
        actual = params.get(param, default)
        for value in values[1:]: # skip the default, which is never a feature.
            features[f"{param}={value}"] = 1.0 if actual == value else 0.0
    return features


def _train_perceptron(
    rows: list[dict[str, float]], targets: list[float], rng: random.Random,
    epochs: int = 300, lr: float = 0.05, l2: float = 0.01,
) -> dict[str, float]:
    """Single linear unit trained by the delta rule (online gradient descent
    on squared error) to predict each sample's distance-from-baseline target
    from its one-hot features. A small L2 penalty keeps weights bounded when
    the sample count is small relative to the number of features (typical
    here: a few dozen samples against ~20 parameters' worth of one-hot
    values). The learned |weight| per feature is the importance signal."""
    keys = list(rows[0].keys())
    weights = {k: 0.0 for k in keys}
    bias = 0.0
    order = list(range(len(rows)))
    for _ in range(epochs):
        rng.shuffle(order)
        for i in order:
            row, target = rows[i], targets[i]
            pred = bias + sum(weights[k] * row[k] for k in keys)
            error = target - pred
            bias += lr * error
            for k in keys:
                if row[k]:
                    weights[k] += lr * (error * row[k] - l2 * weights[k])
    return weights


def _is_prime(n: int) -> bool:
    if n < 2:
        return False
    if n % 2 == 0:
        return n == 2
    i = 3
    while i * i <= n:
        if n % i == 0:
            return False
        i += 2
    return True


def _next_pb_size(min_factors: int, min_dummy: int = 2) -> int:
    """Smallest Plackett-Burman design size (a multiple of 4) that can host
    at least `min_factors` two-level factors *plus* `min_dummy` spare
    ("dummy") columns tied to no real factor, via the Paley construction used
    by _pb_base_design below -- i.e. the smallest x = q + 1 (x a multiple of
    4) with q prime, q % 4 == 3, and q >= min_factors + min_dummy. Such q are
    plentiful (3, 7, 11, 19, 23, 31, 43, ...) so this always terminates
    quickly for realistic parameter counts, but it does mean the achievable
    sizes are a subset of "x divisible by 4" -- e.g. x=16 (q=15, not prime)
    is skipped in favor of x=20.

    The dummy columns exist because Yi, Lilja & Hawkins (Section 2, Table 6)
    deliberately include a couple of "Dummy Parameter" columns bound to no
    real factor in their design: since a dummy column's effect is pure noise
    by construction, its magnitude is the yardstick the paper uses to decide
    whether a *real* parameter's effect is actually distinguishable from
    noise, rather than pruning by an arbitrary fraction of the largest
    effect."""
    x = ((min_factors + min_dummy + 4) // 4) * 4
    while not (_is_prime(x - 1) and (x - 1) % 4 == 3 and x - 1 >= min_factors + min_dummy):
        x += 4
    return x


def _pb_base_design(x: int) -> list[list[int]]:
    """Base (no foldover) Plackett-Burman design: x runs testing up to x-1
    two-level (+-1) factors, via the Paley construction of a Hadamard matrix
    of order x (requires q = x-1 to be prime with q % 4 == 3). Row 0 is the
    quadratic-residue generator row; rows 1..q-1 are its successive circular
    right shifts (the Jacobsthal matrix Q is circulant, and Q+I stays
    circulant); the last row is all -1's -- reproducing, from first
    principles, the classic Plackett & Burman (1946) construction described
    in the paper's Section 2 / Table 1 (whose own generator rows come from a
    hardcoded table instead of being derived here)."""
    q = x - 1
    residues = {(i * i) % q for i in range(1, q)}
    chi = [0] + [1 if a in residues else -1 for a in range(1, q)]  # chi[a], a = 0..q-1

    row0 = [chi[(-j) % q] + (1 if j == 0 else 0) for j in range(q)]
    rows = [row0]
    for _ in range(q - 1):
        prev = rows[-1]
        rows.append([prev[-1]] + prev[:-1])  # circular right shift
    rows.append([-1] * q)
    return rows


def _pb_design_with_foldover(x: int) -> list[list[int]]:
    base = _pb_base_design(x)
    return base + [[-v for v in row] for row in base]


def _pb_entities(
    param_space: dict[str, list],
) -> tuple[list[dict[str, Any]], dict[str, tuple[Any, Any]], list[list[int]], int]:
    """One fully-specified configuration per row of a Plackett-Burman design
    with foldover, for every parameter except branch_predictor_type and its
    own predictor-specific knobs (see module docstring -- those are never
    varied in this mode). Each remaining parameter is assigned its own design
    column and tested at the minimum or maximum of its listed values, rather
    than at two arbitrarily-positioned entries of that list.
    branch_predictor_type stays at its default throughout (simply omitted,
    per the sparse-dict convention), so its own knobs are never active here
    and are excluded from the design entirely. Columns beyond the real
    parameter count are the design's deliberate "dummy
    parameters" (see _next_pb_size) -- kept out of `entities` (they're bound
    to no real parameter) but their raw +-1 values are returned via `design`
    so the caller can compute their effects too, as the noise reference the
    paper's Table 6 uses.

    Returns (entities, levels, design, num_real_columns):
      - levels[param] = (low, high) records which value was which level,
        needed afterwards to sign each run's contribution to that
        parameter's effect.
      - design is the full design matrix (one row per entity, one column per
        PB factor including the dummy ones).
      - num_real_columns is len(columns) -- design columns at or beyond this
        index are dummy columns."""
    columns = [
        p for p in param_space
        if p != "branch_predictor_type" and p not in CONDITIONAL_PARAMS and len(param_space[p]) >= 2
    ]
    levels = {p: (min(param_space[p]), max(param_space[p])) for p in columns}
    x = _next_pb_size(len(columns))
    design = _pb_design_with_foldover(x)

    entities = [
        {param: (levels[param][0] if row[col] == -1 else levels[param][1])
         for col, param in enumerate(columns)}
        for row in design
    ]
    return entities, levels, design, len(columns)


def _screen_cache_path(outputdir: Path) -> Path:
    return outputdir / "preeval" / "screen_cache.json"


def _screen_identity(
    reference_config: str, benchmarks: dict[str, list[str]], alpha: float, method: str,
    num_samples: int, keep_threshold: float, seed: int,
) -> dict:
    """Identifies a screening run for cache matching. plackett_burman is
    fully deterministic given just (config, benchmarks, alpha, PARAM_SPACE)
    -- num_samples/keep_threshold/seed don't affect its result (see
    screen_param_space's docstring), so they're left out of its identity to
    avoid spurious cache misses (e.g. --preeval-samples 1 vs. 5, which that
    method ignores anyway). perceptron's result does depend on those, so
    they're included for it."""
    identity = {
        "reference_config": str(reference_config),
        "benchmarks": benchmarks,
        "alpha": alpha,
        "method": method,
        "full_param_space": PARAM_SPACE,
    }
    if method == "perceptron":
        identity.update(num_samples=num_samples, keep_threshold=keep_threshold, seed=seed)
    return identity


def _read_screen_cache_file(outputdir: Path) -> dict | None:
    path = _screen_cache_path(outputdir)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _load_screen_cache(
    outputdir: Path, identity: dict,
) -> tuple[dict[str, list], dict[frozenset, DesignPoint]] | None:
    raw = _read_screen_cache_file(outputdir)
    if raw is None or raw.get("identity") != identity:
        return None
    global_cache = {params_key(x["params"]): point_from_dict(x) for x in raw["global_cache"]}
    return raw["pruned_param_space"], global_cache


def _save_screen_cache(
    outputdir: Path, identity: dict, pruned_param_space: dict[str, list],
    global_cache: dict[frozenset, DesignPoint],
) -> None:
    path = _screen_cache_path(outputdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, {
        "identity": identity,
        "pruned_param_space": pruned_param_space,
        "global_cache": [point_to_dict(p) for p in global_cache.values()],
    })


def load_screening_cache(
    outputdir: Path, reference_config: str, benchmarks: dict[str, list[str]], alpha: float,
) -> tuple[dict[str, list], dict[frozenset, DesignPoint]]:
    """Load a pruning + evaluated-points cache saved by an earlier
    screen_param_space() call, without re-screening -- backs cli.py's
    --preeval-cache flag, for rerunning the search strategy with different
    strategy flags against an already-computed pruning instead of paying for
    screening's Sniper runs again. Unlike screen_param_space's own internal
    cache reuse (which also matches on method/num_samples/keep_threshold/
    seed), this only checks what must still hold for the pruning to still be
    valid -- config/benchmarks/alpha/PARAM_SPACE -- since the caller isn't
    passing a method to compare against.

    Raises FileNotFoundError/ValueError if no matching cache exists: for a
    flag whose whole point is "reuse the earlier pruning", silently falling
    back to the unpruned PARAM_SPACE would be a confusing surprise."""
    path = _screen_cache_path(outputdir)
    raw = _read_screen_cache_file(outputdir)
    if raw is None:
        raise FileNotFoundError(
            f"--preeval-cache given but no screening cache found at {path} -- "
            "run once with --preeval-samples/--preeval-method first."
        )
    saved = raw["identity"]
    if (saved["reference_config"] != str(reference_config)
            or saved["benchmarks"] != benchmarks
            or saved["alpha"] != alpha
            or saved["full_param_space"] != PARAM_SPACE):
        raise ValueError(
            f"--preeval-cache given but the cache at {path} was computed for a different "
            "config/benchmarks/alpha/param-space -- rerun screening first."
        )
    global_cache = {params_key(x["params"]): point_from_dict(x) for x in raw["global_cache"]}
    return raw["pruned_param_space"], global_cache


def screen_param_space(
    reference_config: str,
    sniper: Path,
    outputdir: Path,
    benchmarks: dict[str, list[str]],
    alpha: float,
    num_samples: int,
    keep_threshold: float = 0.1,
    seed: int = 0,
    method: str = "perceptron",
) -> tuple[dict[str, list], dict[frozenset, DesignPoint]]:
    """Returns (pruned_param_space, global_cache).

    pruned_param_space is a copy of PARAM_SPACE where every parameter judged
    unimportant has been reduced to a single-element list containing just its
    default -- i.e. later strategies will never vary it. `method` selects
    "perceptron" (default, uses `num_samples` random configurations, pruned
    via `keep_threshold` relative to the most important parameter) or
    "plackett_burman" (a Plackett-Burman design instead, see module
    docstring; its parameters are pruned against the design's own
    dummy-column noise ceiling, not `keep_threshold`; branch_predictor_type
    and its own knobs are never varied in this mode, so `keep_threshold` is
    unused by it).

    global_cache holds the baseline plus every successfully evaluated sample
    from this screen, keyed the same way as the search strategies' own
    global_cache (see greedy.params_key). Callers should pass it into the
    chosen strategy's run function so a config re-encountered during the
    actual search -- most likely the baseline itself, since every strategy
    evaluates it first -- is a cache hit instead of a re-run.

    A completed screen is cached to disk and reused here automatically on a
    later call whose identity matches (see _screen_identity) -- so rerunning
    the exact same --preeval-* flags (e.g. only to add an unrelated
    strategy flag) doesn't re-pay for the Sniper runs screening already
    did."""
    identity = _screen_identity(reference_config, benchmarks, alpha, method, num_samples, keep_threshold, seed)
    cached = _load_screen_cache(outputdir, identity)
    if cached is not None:
        pruned_param_space, global_cache = cached
        print(f"=== Pre-evaluation screening: reusing cached result from "
              f"{_screen_cache_path(outputdir)} ({len(global_cache)} cached point"
              f"{'s' if len(global_cache) != 1 else ''}) ===\n")
        return pruned_param_space, global_cache

    rng = random.Random(seed)
    pb_levels: dict[str, tuple[Any, Any]] = {}
    pb_design: list[list[int]] = []
    n_real_columns = 0
    if method == "plackett_burman":
        entities, pb_levels, pb_design, n_real_columns = _pb_entities(PARAM_SPACE)
        n_dummy_columns = (len(pb_design[0]) if pb_design else 0) - n_real_columns
        print(f"=== Pre-evaluation screening (Plackett-Burman design: {len(entities)} runs "
              f"[{n_real_columns} parameters + {n_dummy_columns} dummy columns]; "
              f"branch_predictor_type and its own knobs are never varied in this mode) ===")
    else:
        n_dummy_columns = 0
        entities = [_random_entity(rng, PARAM_SPACE) for _ in range(num_samples)]
        print(f"=== Pre-evaluation screening ({num_samples} samples) ===")

    print("Running baseline...")
    baseline_dir = outputdir / "preeval" / "baseline"
    baseline = compute_baseline(reference_config, sniper, baseline_dir, benchmarks)

    global_cache = {params_key(DEFAULTS): baseline}
    # baseline_dir is deliberately left out of sample_dirs: the baseline is
    # the one point a resumed strategy is guaranteed to look up in
    # global_cache (every strategy evaluates it first), so its output is kept
    # on disk rather than cleaned up like the rest of the screening samples.
    sample_dirs: set[Path] = set()
    rows: list[dict[str, float]] = []
    targets: list[float] = []
    pb_effects_asi: dict[str, float] = {param: 0.0 for param in pb_levels}
    pb_effects_speedup: dict[str, float] = {param: 0.0 for param in pb_levels}
    pb_dummy_effects_asi: list[float] = [0.0] * n_dummy_columns
    pb_dummy_effects_speedup: list[float] = [0.0] * n_dummy_columns
    pb_successes = 0

    for i, params in enumerate(entities):
        modified = {p for p, v in params.items() if v != DEFAULTS[p]}
        out = outputdir / "preeval" / f"sample{i}"
        sample_dirs.add(out)
        point, _, _ = evaluate_point(
            params, modified, out, reference_config, sniper, benchmarks, baseline, alpha, global_cache,
        )
        if point is None:
            continue
        print_evaluated_point(params, point, prefix="[preeval] ")
        if method == "plackett_burman":
            pb_successes += 1
            asi_dev = point.asi - 1.0
            speedup_dev = point.speedup - 1.0
            for param, value in params.items():
                low, _high = pb_levels[param]
                sign = -1.0 if value == low else 1.0
                pb_effects_asi[param] += sign * asi_dev
                pb_effects_speedup[param] += sign * speedup_dev
            row = pb_design[i]
            for d in range(n_dummy_columns):
                pb_dummy_effects_asi[d] += row[n_real_columns + d] * asi_dev
                pb_dummy_effects_speedup[d] += row[n_real_columns + d] * speedup_dev
        else:
            target = ((point.asi - 1.0) ** 2 + (point.speedup - 1.0) ** 2) ** 0.5
            rows.append(_encode_features(params, PARAM_SPACE))
            targets.append(target)

    n = cleanup_dirs(sample_dirs)
    if n:
        print(f"  Deleted {n} pre-evaluation output director{'y' if n == 1 else 'ies'}.")

    if method == "plackett_burman":
        pruned_param_space, global_cache = _screen_plackett_burman(
            PARAM_SPACE, pb_levels, pb_effects_asi, pb_effects_speedup,
            pb_dummy_effects_asi, pb_dummy_effects_speedup, pb_successes, global_cache,
        )
    elif len(rows) < 2:
        print("  Not enough successful samples to screen -- keeping full param space.\n")
        pruned_param_space = PARAM_SPACE
    else:
        weights = _train_perceptron(rows, targets, rng)
        importance: dict[str, float | None] = {
            param: max((abs(weights[f"{param}={v}"]) for v in values[1:]), default=0.0)
            for param, values in PARAM_SPACE.items()
        }
        max_importance = max((v for v in importance.values() if v is not None), default=0.0)

        print("\n  Parameter importance (perceptron |weight|):")
        pruned: dict[str, list] = {}
        order = sorted(PARAM_SPACE, key=lambda p: -(importance[p] if importance[p] is not None else float("inf")))
        for param in order:
            values = PARAM_SPACE[param]
            imp = importance[param]
            keep = imp is None or max_importance == 0.0 or imp >= keep_threshold * max_importance
            pruned[param] = values if keep else [values[0]]
            shown = "n/a (insufficient data)" if imp is None else f"{imp:.4f}"
            print(f"    {param:<28} {shown:>10}  [{'kept' if keep else 'pruned'}]")
        print()

        pruned_param_space = {param: pruned[param] for param in PARAM_SPACE}

    _save_screen_cache(outputdir, identity, pruned_param_space, global_cache)
    return pruned_param_space, global_cache


def _screen_plackett_burman(
    param_space: dict[str, list],
    pb_levels: dict[str, tuple[Any, Any]],
    pb_effects_asi: dict[str, float],
    pb_effects_speedup: dict[str, float],
    pb_dummy_effects_asi: list[float],
    pb_dummy_effects_speedup: list[float],
    pb_successes: int,
    global_cache: dict[frozenset, DesignPoint],
) -> tuple[dict[str, list], dict[frozenset, DesignPoint]]:
    """Applies Yi, Lilja & Hawkins' actual significance rule (Section 2,
    Table 6) to the Plackett-Burman screen run by screen_param_space(method=
    "plackett_burman"): a parameter is judged against the noise ceiling set
    by the design's own dummy columns -- pb_dummy_effects_{asi,speedup} are
    computed exactly like a real parameter's effect (sum of +-1 times each
    run's *signed* deviation from baseline, per objective) but are tied to no
    real factor, so their magnitude *is* what pure noise looks like at this
    sample size, separately for each objective. A parameter is kept if
    *either* objective's |effect| beats that objective's own largest dummy
    effect -- a parameter that only moves ASI (or only speedup) still matters
    to the Pareto front, and judging both objectives through one combined
    unsigned distance let a single saturating parameter (e.g. rob_window_size
    at its crippling low extreme) dominate every column's effect at once,
    producing an unstable all-kept-or-all-pruned split instead of a real
    ranking.

    branch_predictor_type and its own predictor-specific knobs are never
    part of this design (see module docstring) -- no evaluations are spent
    judging them, but they're left untouched (full range, not pruned) here so
    the search strategy that runs afterward can still explore them."""
    branch_predictor_params = {"branch_predictor_type"} | CONDITIONAL_PARAMS
    pruned: dict[str, list] = {p: v for p, v in param_space.items() if p not in branch_predictor_params}

    if pb_successes < 2:
        print("  Not enough successful Plackett-Burman runs -- keeping those parameters.\n")
    else:
        ceiling_asi = max((abs(e) for e in pb_dummy_effects_asi), default=0.0)
        ceiling_speedup = max((abs(e) for e in pb_dummy_effects_speedup), default=0.0)
        print(f"\n  Parameter importance (PB |effect| on asi / speedup, "
              f"dummy-column noise ceilings={ceiling_asi:.4f} / {ceiling_speedup:.4f}):")
        order = sorted(
            pb_levels,
            key=lambda p: -max(
                abs(pb_effects_asi[p]) / ceiling_asi if ceiling_asi else float(pb_effects_asi[p] != 0),
                abs(pb_effects_speedup[p]) / ceiling_speedup if ceiling_speedup else float(pb_effects_speedup[p] != 0),
            ),
        )
        for param in order:
            imp_asi = abs(pb_effects_asi[param])
            imp_speedup = abs(pb_effects_speedup[param])
            keep = imp_asi > ceiling_asi or imp_speedup > ceiling_speedup
            pruned[param] = param_space[param] if keep else [param_space[param][0]]
            print(f"    {param:<28} asi={imp_asi:>9.4f} speedup={imp_speedup:>9.4f}  [{'kept' if keep else 'pruned'}]")
        print()

    for param in branch_predictor_params:
        if param in param_space:
            pruned[param] = param_space[param]

    return {param: pruned[param] for param in param_space}, global_cache
