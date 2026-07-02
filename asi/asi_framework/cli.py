import argparse
import contextlib
import sys
from pathlib import Path

from .config import RUN_SNIPER, DEFAULT_OUTPUT_DIR, DEFAULT_ALPHA
from .search import explore_pareto_front_with_sensitivity, print_pareto_table
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
    parser.add_argument("--iterations", type=int, default=10,
                        help="Maximum number of search iterations.")
    parser.add_argument("--log", nargs="?", const="auto", metavar="PATH",
                        help="Save terminal output to PATH. Omit PATH to use outputdir/run.log.")
    parser.add_argument("--save-plot", nargs="?", const="auto", metavar="PATH",
                        help="Save the final plot to PATH. Omit PATH to use outputdir/pareto.png.")
    return parser


def main() -> int:
    parser = build_parser()

    argv = sys.argv[1:]
    if "--" not in argv:
        parser.error("benchmark command must be separated with '--', "
                      "e.g. asi.py --config c.cfg --log --save-plot -- ./bench")
    split = argv.index("--")
    args = parser.parse_args(argv[:split])
    args.cmd = argv[split + 1:]
    if not args.cmd:
        parser.error("no benchmark command given after '--'")

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

    with (_tee_stdout(log_path) if log_path else contextlib.nullcontext()):
        if log_path:
            print(f"Logging to {log_path}\n")

        front = explore_pareto_front_with_sensitivity(
            reference_config=args.config,
            sniper=sniper,
            outputdir=outputdir,
            cmd=args.cmd,
            alpha=args.alpha,
            max_iterations=args.iterations,
        )

        print("=== Final Pareto Front ===")
        print_pareto_table(front)

    plot_pareto_front_on_asi(front, title="ASI Pareto Front", save_path=save_plot)
    return 0
