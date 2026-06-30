import argparse
from pathlib import Path

from .config import RUN_SNIPER, DEFAULT_OUTPUT_DIR, DEFAULT_ALPHA
from .search import explore_pareto_front_with_sensitivity


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asi.py",
        description="Algorithm launcher for design space exploration.",
    )
    parser.add_argument("--config", required=True, help="Reference design config file for Sniper.")
    parser.add_argument("--sniper", default=str(RUN_SNIPER), help="Path to the run-sniper entry point.")
    parser.add_argument("--outputdir", "-d", default=str(DEFAULT_OUTPUT_DIR), help="Output directory.")
    parser.add_argument("--alpha", type=float, default=DEFAULT_ALPHA, help="Alpha for ASI formula.")
    parser.add_argument("--show-config", action="store_true", help="Print config and exit.")
    parser.add_argument("cmd", nargs=argparse.REMAINDER, help="Command to run after --.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    sniper = Path(args.sniper).expanduser().resolve()
    if not sniper.exists():
        parser.error(f"run-sniper not found: {sniper}")

    outputdir = Path(args.outputdir).expanduser().resolve()
    outputdir.mkdir(parents=True, exist_ok=True)

    front = explore_pareto_front_with_sensitivity(
        reference_config=args.config,
        sniper=sniper,
        outputdir=outputdir,
        cmd=args.cmd,
        alpha=args.alpha,
        max_iterations=10,
    )

    print("\n=== Final Pareto Front ===")
    print(f"{'Params':<55} {'ASI':>8} {'Speedup':>10} {'Area':>10} {'PeakPow':>10}")
    print("-" * 100)
    for p in sorted(front, key=lambda x: x.speedup):
        print(f"{str(p.params):<55} {p.asi:>8.4f} {p.speedup:>10.4f} "
              f"{p.area:>10.4f} {p.peak_power:>10.4f}")

    return 0