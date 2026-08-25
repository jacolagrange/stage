import argparse
import contextlib
import inspect
import sys
from pathlib import Path

from .config import RUN_SNIPER, DEFAULT_OUTPUT_DIR, DEFAULT_ALPHA, DEFAULTS
from . import greedy, spea2, mesmo, screening
from .strategies import STRATEGIES
from .plot import plot_pareto_front_on_asi


class _Tee:
    """Write to multiple streams simultaneously."""
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data: str) -> None:
        for s in self._streams:
            s.write(data)

    def flush(self) -> None:
        for s in self._streams:
            s.flush()

    def isatty(self) -> bool:
        return False


@contextlib.contextmanager
def _tee_stdout(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        old = sys.stdout
        sys.stdout = _Tee(old, f)
        try:
            yield
        finally:
            sys.stdout = old


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asi.py",
        description="ASI-guided design space exploration for processor microarchitectures.",
        epilog="Example:\n"
               "  asi.py --config nehalem.cfg --strategy spea2 --log --save-plot -- ./bench_a arg1\n\n"
               "One or more benchmark commands must follow '--'; repeat '-- ./other_bench ...'\n"
               "to search across multiple benchmarks at once.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", required=True, help="Reference Sniper config file.")
    parser.add_argument("--sniper", default=str(RUN_SNIPER), help="Path to run-sniper.")
    parser.add_argument("--outputdir", "-d", default=str(DEFAULT_OUTPUT_DIR), help="Output directory.")
    parser.add_argument("--alpha", type=float, default=DEFAULT_ALPHA,
                        help="Alpha weight for ASI (0=operational, 1=embodied).")
    parser.add_argument("--strategy", choices=sorted(STRATEGIES), default="greedy",
                        help="Search strategy: 'greedy' (sensitivity-freezing hill-climb, default), "
                             "'spea2' (COLE-style multi-objective evolutionary search), 'mesmo' "
                             "(max-value entropy search multi-objective Bayesian optimization), or "
                             "'hybrid' (mesmo until its hypervolume plateaus, then spea2 seeded from "
                             "mesmo's final Pareto front instead of a random one).")
    parser.add_argument("--iterations", type=int, default=None,
                        help="Maximum number of search iterations (greedy) / generations (spea2, "
                             "and hybrid's spea2 phase) / BO iterations (mesmo). Defaults to whichever "
                             "strategy is selected picking its own default (5 for greedy, 30 for "
                             "spea2/mesmo/hybrid) rather than one fixed number for every strategy. For "
                             "hybrid's mesmo-phase iteration budget, see --mesmo-phase-iterations.")
    parser.add_argument("--log", nargs="?", const="auto", metavar="PATH",
                        help="Save terminal output to PATH. Omit PATH to use outputdir/run.log.")
    parser.add_argument("--save-plot", nargs="?", const="auto", metavar="PATH",
                        help="Save the final plot to PATH. Omit PATH to use outputdir/pareto.png.")

    spea2_group = parser.add_argument_group("spea2 strategy options")
    spea2_group.add_argument("--populations", type=int, default=3, dest="num_populations",
                              help="Number of SPEA2 populations.")
    spea2_group.add_argument("--population-size", type=int, default=20,
                              help="Entities per SPEA2 population.")
    spea2_group.add_argument("--archive-size", type=int, default=10,
                              help="Pareto-archive size per SPEA2 population.")
    spea2_group.add_argument("--p-mutation", type=float, default=0.10,
                              help="Per-child probability of mutating one parameter.")
    spea2_group.add_argument("--p-crossover", type=float, default=0.90,
                              help="Per-child probability of crossover (vs. plain copy).")
    spea2_group.add_argument("--p-migration", type=float, default=0.10,
                              help="Per mating-pool slot probability of drawing from another population.")
    spea2_group.add_argument("--patience", type=int, default=5,
                              help="Stop after this many generations with no hypervolume improvement "
                                   "(spea2 only -- see --mesmo-patience for mesmo).")
    spea2_group.add_argument("--seed", type=int, default=0,
                              help="RNG seed for reproducible runs (spea2, mesmo).")

    titan_group = parser.add_argument_group("titan strategy options (spea2 only)")
    titan_group.add_argument("--titan", action="store_true",
                              help="Evaluate each spea2 generation as one batch job on Titan "
                                   "(titan_controller) instead of one local Sniper run at a time. "
                                   "Requires --titan-benchmark-json.")
    titan_group.add_argument("--titan-benchmark-json", dest="titan_benchmark_json",
                              help="titan_controller benchmark JSON covering the same benchmark "
                                   "names given after '--' (required with --titan).")
    titan_group.add_argument("--titan-dir", dest="titan_dir",
                              help="Path to the titan_controller checkout. Defaults to "
                                   "'titan_controller' next to this repo.")
    titan_group.add_argument("--titan-host-dir", dest="titan_host_dir",
                              help="Where Titan results land locally. Defaults to outputdir/titan.")
    titan_group.add_argument("--titan-sniper-mount", dest="titan_sniper_mount",
                              default="/mnt/perflab/exascience/src/jaco_sniper",
                              help="Sniper checkout mounted on Titan compute nodes.")
    titan_group.add_argument("--titan-benchmarks-mount", dest="titan_benchmarks_mount",
                              default="/mnt/perflab/exascience/src/jaco_benchmarks",
                              help="Benchmarks checkout mounted on Titan compute nodes.")
    titan_group.add_argument("--titan-poll-interval", dest="titan_poll_interval",
                              type=float, default=30.0,
                              help="Seconds between --list job polls while waiting on a batch.")

    mesmo_group = parser.add_argument_group("mesmo strategy options")
    mesmo_group.add_argument("--num-initial-points", type=int, default=5,
                              help="Random configurations (plus the baseline) evaluated up front to "
                                   "seed MESMO's GP surrogate before the first acquisition-guided "
                                   "iteration.")
    mesmo_group.add_argument("--candidate-pool-size", type=int, default=200,
                              help="Fresh not-yet-evaluated configurations sampled each iteration and "
                                   "scored with the acquisition function (stands in for MESMO's "
                                   "argmax over the whole PARAM_SPACE). Roughly half are local "
                                   "single-parameter neighbors of the current Pareto front, half fresh "
                                   "global random draws.")
    mesmo_group.add_argument("--batch-size", type=int, default=1,
                              help="Top-ranked candidates evaluated per iteration before refitting the "
                                   "GP surrogate (1 = sequential MESMO as in the paper; >1 trades some "
                                   "acquisition accuracy for fewer, batched refits).")
    mesmo_group.add_argument("--mc-samples", type=int, default=10, dest="num_mc_samples",
                              help="Monte-Carlo samples of the Pareto front used to estimate MESMO's "
                                   "acquisition function per candidate (S in the paper).")
    mesmo_group.add_argument("--gp-features", type=int, default=250,
                              help="Random Fourier features used to approximate each objective's GP "
                                   "surrogate.")
    mesmo_group.add_argument("--gp-lengthscale", type=float, default=None,
                              help="Kernel lengthscale for the GP surrogates. Defaults to a "
                                   "median-heuristic bandwidth re-derived from the real evaluations seen "
                                   "so far each iteration.")
    mesmo_group.add_argument("--gp-noise", type=float, default=1e-4,
                              help="GP observation noise variance (in standardized objective units).")
    mesmo_group.add_argument("--mesmo-patience", type=int, default=None, dest="hv_patience",
                              help="Stop after this many iterations with no hypervolume improvement. "
                                   "Unset by default (runs the full --iterations budget), matching the "
                                   "paper's fixed-budget algorithm -- unlike spea2's --patience, a mesmo "
                                   "iteration can evaluate as little as one point, so a short patience "
                                   "window would trigger on ordinary exploration noise.")

    hybrid_group = parser.add_argument_group("hybrid strategy options")
    hybrid_group.add_argument("--mesmo-phase-iterations", type=int, default=30, dest="mesmo_max_iterations",
                               help="Max MESMO iterations in hybrid's exploration phase before "
                                    "switching to SPEA2 (hybrid only) -- the phase usually stops "
                                    "earlier once its hypervolume plateaus (see --mesmo-patience, "
                                    "shared with plain 'mesmo', but defaulting to 5 instead of unset "
                                    "when the strategy is 'hybrid', since plateau detection is this "
                                    "phase's whole point). --iterations/--populations/etc. above "
                                    "configure hybrid's subsequent spea2 phase.")

    preeval_group = parser.add_argument_group("pre-evaluation screening options")
    preeval_group.add_argument("--preeval-samples", type=int, default=0,
                                help="If >0, screen PARAM_SPACE for important parameters before "
                                     "running the search strategy (runs independently of "
                                     "--strategy). Unimportant parameters are pruned to their "
                                     "default-only value (see --preeval-threshold and --preeval-method "
                                     "for how importance is judged). 0 disables screening (default). "
                                     "Sets the sample count for --preeval-method perceptron; with "
                                     "plackett_burman it's just the on/off switch (e.g. 1) -- that "
                                     "method's run count is fixed by the parameter count instead.")
    preeval_group.add_argument("--preeval-method", choices=("perceptron", "plackett_burman"),
                                default="perceptron",
                                help="Screening method: 'perceptron' (default) trains a linear unit "
                                     "on --preeval-samples random configurations; 'plackett_burman' "
                                     "instead screens most parameters with a Plackett-Burman design "
                                     "with foldover (Yi, Lilja & Hawkins, Section 2), plus a small "
                                     "deterministic one-value-at-a-time sweep just for "
                                     "branch_predictor_type and its own knobs, since that's a "
                                     ">2-valued categorical choice a 2-level PB factor can't represent.")
    preeval_group.add_argument("--preeval-threshold", type=float, default=0.1,
                                help="Keep a parameter only if its estimated importance is at least "
                                     "this fraction of the most important parameter's (default 0.1). "
                                     "Used by --preeval-method perceptron, and by plackett_burman's "
                                     "branch-predictor one-at-a-time screen; plackett_burman's main "
                                     "parameters are instead pruned against their own design's "
                                     "dummy-column noise ceiling (Yi, Lilja & Hawkins, Table 6).")
    preeval_group.add_argument("--preeval-seed", type=int, default=0,
                                help="RNG seed for pre-evaluation sampling (default 0). Ignored by "
                                     "--preeval-method plackett_burman, which is fully deterministic.")
    preeval_group.add_argument("--preeval-cache", action="store_true",
                                help="Reuse the pruned param space + evaluated points a previous "
                                     "--preeval-samples run already cached in this outputdir "
                                     "(outputdir/preeval/screen_cache.json) instead of screening "
                                     "again -- lets you rerun the search strategy with different "
                                     "strategy flags without repeating pre-evaluation screening's "
                                     "Sniper runs. Errors out if no cache matching --config/the "
                                     "benchmark commands/--alpha exists yet. Mutually exclusive "
                                     "with --preeval-samples, which already reuses a matching cache "
                                     "automatically and re-screens (then re-caches) otherwise.")
    return parser


def _benchmark_name(cmd: list[str], used: set[str]) -> str:
    """Derive a display/output-dir name for a benchmark command from its
    executable's parent directory (matching the benchmarks/<NAME>/bench
    layout), de-duplicating if the same name would be used twice."""
    exe = Path(cmd[0])
    stem = exe.resolve().parent.name or exe.stem or "bench"
    name = stem
    suffix = 2
    while name in used:
        name = f"{stem}_{suffix}"
        suffix += 1
    used.add(name)
    return name


def main() -> int:
    parser = build_parser()

    argv = sys.argv[1:]
    if "--" not in argv:
        parser.error("benchmark command(s) must be separated with '--', "
                      "e.g. asi.py --config c.cfg --log --save-plot -- ./bench "
                      "(repeat '-- ./other_bench ...' to search across multiple benchmarks)")
    separators = [i for i, a in enumerate(argv) if a == "--"]
    args = parser.parse_args(argv[:separators[0]])

    bounds = separators + [len(argv)]
    segments = [argv[s + 1:e] for s, e in zip(bounds[:-1], bounds[1:])]
    segments = [seg for seg in segments if seg]
    if not segments:
        parser.error("no benchmark command given after '--'")

    used_names: set[str] = set()
    args.benchmarks = {_benchmark_name(seg, used_names): seg for seg in segments}

    if args.preeval_cache and args.preeval_samples > 0:
        parser.error("--preeval-cache and --preeval-samples are mutually exclusive -- "
                      "--preeval-samples already reuses a matching cache automatically; "
                      "--preeval-cache is for reusing one without passing --preeval-samples "
                      "(or --preeval-method) at all.")

    sniper = Path(args.sniper).expanduser().resolve()
    if not sniper.exists():
        parser.error(f"run-sniper not found: {sniper}")

    outputdir = Path(args.outputdir).expanduser().resolve()
    outputdir.mkdir(parents=True, exist_ok=True)

    log_path = None
    if args.log is not None:
        log_path = outputdir / "run.log" if args.log == "auto" else Path(args.log)

    save_plot = None
    if args.save_plot is not None:
        save_plot = outputdir / "pareto.png" if args.save_plot == "auto" else Path(args.save_plot)

    strategy = STRATEGIES[args.strategy]
    base_kwargs = {
        "reference_config": args.config,
        "sniper": sniper,
        "outputdir": outputdir,
        "benchmarks": args.benchmarks,
        "alpha": args.alpha,
    }
    if args.iterations is not None:
        base_kwargs["max_iterations"] = args.iterations

    accepted = inspect.signature(strategy.run).parameters
    extra_kwargs = {k: v for k, v in vars(args).items() if k in accepted and k not in base_kwargs}

    with (_tee_stdout(log_path) if log_path else contextlib.nullcontext()):
        if log_path:
            print(f"Logging to {log_path}\n")

        print("Benchmarks: " + ", ".join(
            f"{name} ({' '.join(cmd)})" for name, cmd in args.benchmarks.items()
        ) + "\n")

        if args.preeval_samples > 0 or args.preeval_cache:
            if args.preeval_cache:
                pruned_param_space, preeval_cache = screening.load_screening_cache(
                    outputdir=outputdir,
                    reference_config=args.config,
                    benchmarks=args.benchmarks,
                    alpha=args.alpha,
                )
            else:
                pruned_param_space, preeval_cache = screening.screen_param_space(
                    reference_config=args.config,
                    sniper=sniper,
                    outputdir=outputdir,
                    benchmarks=args.benchmarks,
                    alpha=args.alpha,
                    num_samples=args.preeval_samples,
                    keep_threshold=args.preeval_threshold,
                    seed=args.preeval_seed,
                    method=args.preeval_method,
                )
            greedy.PARAM_SPACE = pruned_param_space
            spea2.PARAM_SPACE = pruned_param_space
            mesmo.PARAM_SPACE = pruned_param_space
            base_kwargs["initial_cache"] = preeval_cache

            if "seed_entities" in accepted:
                baseline_key = greedy.params_key(DEFAULTS)
                preeval_points = [p for k, p in preeval_cache.items() if k != baseline_key]
                base_kwargs["seed_entities"] = [dict(p.params) for p in preeval_points]
                if preeval_points and "preeval_runs" in accepted:
                    base_kwargs["preeval_runs"] = len(preeval_points)
                    base_kwargs["preeval_invocations"] = sum(len(p.per_benchmark) for p in preeval_points)

        front = strategy.run(**base_kwargs, **extra_kwargs)

        print("=== Final Pareto Front ===")
        greedy.print_pareto_table(front)

    plot_pareto_front_on_asi(front, title="ASI Pareto Front", save_path=save_plot, show=False)
    return 0
