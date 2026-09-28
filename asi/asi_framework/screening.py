"""Parameter-importance pre-screen (perceptron or Plackett-Burman), run once
before any search strategy."""
import json
import random
from pathlib import Path
from typing import Any

from .config import PARAM_SPACE, DEFAULTS, CONDITIONAL_PARAMS
from .models import DesignPoint
from .metrics import params_key
from .evaluation import evaluate_point, compute_baseline
from .display import print_evaluated_point
from .search_ops import random_entity
from .state import cleanup_dirs, write_json_atomic, point_to_dict, point_from_dict
from . import titan_batch


def _encode_features(params: dict[str, Any], param_space: dict[str, list]) -> dict[str, float]:
    """One binary feature per (param, non-default value) pair."""
    features = {}
    for param, values in param_space.items():
        default = values[0]
        actual = params.get(param, default)
        for value in values[1:]:
            features[f"{param}={value}"] = 1.0 if actual == value else 0.0
    return features


def _train_perceptron(
    rows: list[dict[str, float]], targets: list[float], rng: random.Random,
    epochs: int = 300, lr: float = 0.05, l2: float = 0.01,
) -> dict[str, float]:
    """Single linear unit trained online (delta rule + L2) to predict each
    sample's distance-from-baseline target from its one-hot features."""
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
    """Smallest Plackett-Burman design size (x = q+1, q prime, q%4==3) that
    fits min_factors two-level factors plus min_dummy noise-reference columns."""
    x = ((min_factors + min_dummy + 4) // 4) * 4
    while not (_is_prime(x - 1) and (x - 1) % 4 == 3 and x - 1 >= min_factors + min_dummy):
        x += 4
    return x


def _pb_base_design(x: int) -> list[list[int]]:
    """Base (no foldover) Plackett-Burman design via the Paley construction
    of a Hadamard matrix of order x (q = x-1 prime, q%4==3)."""
    q = x - 1
    residues = {(i * i) % q for i in range(1, q)}
    chi = [0] + [1 if a in residues else -1 for a in range(1, q)]

    row0 = [chi[(-j) % q] + (1 if j == 0 else 0) for j in range(q)]
    rows = [row0]
    for _ in range(q - 1):
        prev = rows[-1]
        rows.append([prev[-1]] + prev[:-1])
    rows.append([-1] * q)
    return rows


def _pb_design_with_foldover(x: int) -> list[list[int]]:
    base = _pb_base_design(x)
    return base + [[-v for v in row] for row in base]


def _pb_entities(
    param_space: dict[str, list],
) -> tuple[list[dict[str, Any]], dict[str, tuple[Any, Any]], list[list[int]], int]:
    """One config per Plackett-Burman-with-foldover design row, min/max per
    parameter (branch_predictor_type and its own knobs excluded, left at
    default). Returns (entities, levels, design, num_real_columns) -- levels
    records which value was which level; design columns >= num_real_columns
    are dummy (noise-reference) columns."""
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
    deterministic given (config, benchmarks, alpha, PARAM_SPACE) alone;
    perceptron's result also depends on num_samples/keep_threshold/seed."""
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
    """Loads a screen_param_space() cache without re-screening (backs
    --preeval-cache). Raises FileNotFoundError/ValueError if no cache
    matching config/benchmarks/alpha/PARAM_SPACE exists."""
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
    titan: bool = False,
    titan_benchmark_json: str | None = None,
    titan_dir: str | None = None,
    titan_host_dir: str | None = None,
    titan_sniper_mount: str = "/mnt/perflab/exascience/src/jaco_sniper",
    titan_benchmarks_mount: str = "/mnt/perflab/exascience/src/jaco_benchmarks",
    titan_poll_interval: float = 30.0,
) -> tuple[dict[str, list], dict[frozenset, DesignPoint]]:
    """Returns (pruned_param_space, global_cache) -- pruned_param_space
    reduces every unimportant parameter to its default-only value; method
    is "perceptron" or "plackett_burman". Cached to disk and reused on a
    later call with matching identity (_screen_identity)."""
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
        entities = [random_entity(rng, PARAM_SPACE) for _ in range(num_samples)]
        print(f"=== Pre-evaluation screening ({num_samples} samples) ===")

    print("Running baseline...")
    baseline_dir = outputdir / "preeval" / "baseline"
    baseline = compute_baseline(reference_config, sniper, baseline_dir, benchmarks)

    global_cache = {params_key(DEFAULTS): baseline}
    sample_dirs: set[Path] = set()
    rows: list[dict[str, float]] = []
    targets: list[float] = []
    pb_effects_asi: dict[str, float] = {param: 0.0 for param in pb_levels}
    pb_effects_speedup: dict[str, float] = {param: 0.0 for param in pb_levels}
    pb_dummy_effects_asi: list[float] = [0.0] * n_dummy_columns
    pb_dummy_effects_speedup: list[float] = [0.0] * n_dummy_columns
    pb_successes = 0

    entries = []
    for i, params in enumerate(entities):
        modified = {p for p, v in params.items() if v != DEFAULTS[p]}
        out = outputdir / "preeval" / f"sample{i}"
        sample_dirs.add(out)
        entries.append((params, out, modified))

    titan_config = titan_batch.build_config(
        titan, outputdir, titan_benchmark_json, titan_dir, titan_host_dir,
        titan_sniper_mount, titan_benchmarks_mount, titan_poll_interval,
    )
    if titan_config is None:
        points = [
            evaluate_point(params, modified, out, reference_config, sniper, benchmarks, baseline, alpha, global_cache)[0]
            for params, out, modified in entries
        ]
    else:
        points = [point for point, _, _ in titan_batch.evaluate_batch(
            entries, reference_config, benchmarks, baseline, alpha, global_cache,
            titan_controller_dir=titan_config["titan_controller_dir"],
            benchmark_json_path=titan_config["benchmark_json_path"],
            host_destination_path=titan_config["host_destination_path"] / "preeval",
            sniper_mount=titan_config["sniper_mount"],
            benchmarks_mount=titan_config["benchmarks_mount"],
            poll_interval=titan_config["poll_interval"],
            job_name="asi_preeval",
        )]

    for i, ((params, _out, _modified), point) in enumerate(zip(entries, points)):
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
    """Keeps a parameter if either objective's |effect| beats that
    objective's own dummy-column noise ceiling (Yi, Lilja & Hawkins Table 6).
    branch_predictor_type and its own knobs are left untouched (unpruned)."""
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
