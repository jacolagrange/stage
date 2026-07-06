import argparse
import contextlib
import inspect
import sys
from pathlib import Path

from .config import RUN_SNIPER, DEFAULT_OUTPUT_DIR, DEFAULT_ALPHA
from .greedy import print_pareto_table
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
    )
    parser.add_argument("--config", required=True, help="Reference Sniper config file.")
    parser.add_argument("--sniper", default=str(RUN_SNIPER), help="Path to run-sniper.")
    parser.add_argument("--outputdir", "-d", default=str(DEFAULT_OUTPUT_DIR), help="Output directory.")
    parser.add_argument("--alpha", type=float, default=DEFAULT_ALPHA,
                        help="Alpha weight for ASI (0=operational, 1=embodied).")
    parser.add_argument("--strategy", choices=sorted(STRATEGIES), default="greedy",
                        help="Search strategy: 'greedy' (sensitivity-freezing hill-climb, default) "
                             "or 'spea2' (COLE-style multi-objective evolutionary search).")
    parser.add_argument("--iterations", type=int, default=10,
                        help="Maximum number of search iterations (greedy) / generations (spea2).")
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
                              help="Stop after this many generations with no hypervolume improvement.")
    spea2_group.add_argument("--seed", type=int, default=0,
                              help="RNG seed for reproducible SPEA2 runs.")
    return parser


def _benchmark_name(cmd: list[str], used: set[str]) -> str:
    """Derive a display/output-dir name for a benchmark command from its
    executable's parent directory (matching the libs/benchmarks/<NAME>/bench
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
        "max_iterations": args.iterations,
    }
    # Forward any strategy-specific CLI flags (e.g. --populations, --seed) whose
    # dest name matches a parameter the chosen strategy's run function accepts.
    # This is what lets a future strategy plug in without touching this dispatch.
    accepted = inspect.signature(strategy.run).parameters
    extra_kwargs = {k: v for k, v in vars(args).items() if k in accepted and k not in base_kwargs}

    with (_tee_stdout(log_path) if log_path else contextlib.nullcontext()):
        if log_path:
            print(f"Logging to {log_path}\n")

        print("Benchmarks: " + ", ".join(
            f"{name} ({' '.join(cmd)})" for name, cmd in args.benchmarks.items()
        ) + "\n")

        front = strategy.run(**base_kwargs, **extra_kwargs)

        print("=== Final Pareto Front ===")
        print_pareto_table(front)

    plot_pareto_front_on_asi(front, title="ASI Pareto Front", save_path=save_plot)
    return 0
