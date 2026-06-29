#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RUN_SNIPER = ROOT / "snipersim" / "run-sniper"
DEFAULT_CONFIG = "gainestown"
DEFAULT_CORE_MODEL = "nehalem"
DEFAULT_CORE_TYPE = "rob"
DEFAULT_FREQUENCY = 2.66
DEFAULT_LOGICAL_CPUS = 1
DEFAULT_L1I_SIZE = 32
DEFAULT_L1D_SIZE = 32
DEFAULT_L2_SIZE = 256
DEFAULT_L3_SIZE = 8192
DEFAULT_L1I_ASSOC = 4
DEFAULT_L1D_ASSOC = 8
DEFAULT_L2_ASSOC = 8
DEFAULT_L3_ASSOC = 16
DEFAULT_BRANCH_PREDICTOR_TYPE = "pentium_m"
DEFAULT_BRANCH_MISPREDICT_PENALTY = 8
DEFAULT_BRANCH_PREDICTOR_SIZE = 1024


def quote_cfg(value: object) -> str:
	return str(value)


def add_cfg_arg(args: list[str], key: str, value: object | None) -> None:
	if value is None:
		return
	args.extend(["-c", f"{key}={quote_cfg(value)}"])


def add_cfg_arg_if_changed(
	args: list[str], key: str, value: object | None, default: object
) -> None:
	if value is None or value == default:
		return
	add_cfg_arg(args, key, value)


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(
		prog="run.py",
		description="Sniper launcher for microarchitecture options.",
	)
	parser.add_argument(
		"--sniper",
		default=str(RUN_SNIPER),
		help="Path to the run-sniper entry point.",
	)
	parser.add_argument(
		"--cores",
		"-n",
		type=int,
		default=1,
		help="Total simulated cores (general/total_cores).",
	)
	parser.add_argument(
		"--outputdir",
		"-d",
		default=str(ROOT / "output"),
		help="Sniper output directory.",
	)
	parser.add_argument(
		"--core-model",
		default=DEFAULT_CORE_MODEL,
		help="Core model name for perf_model/core/core_model.",
	)
	parser.add_argument(
		"--core-type",
		default=DEFAULT_CORE_TYPE,
		help="Core timing model for perf_model/core/type.",
	)
	parser.add_argument(
		"--frequency",
		type=float,
		default=DEFAULT_FREQUENCY,
		help="Core frequency in GHz.",
	)
	parser.add_argument(
		"--logical-cpus",
		type=int,
		default=DEFAULT_LOGICAL_CPUS,
		help="SMT threads per core.",
	)
	parser.add_argument(
		"--l1i-size",
		type=int,
		default=DEFAULT_L1I_SIZE,
		help="L1 instruction cache size in KB.",
	)
	parser.add_argument(
		"--l1d-size",
		type=int,
		default=DEFAULT_L1D_SIZE,
		help="L1 data cache size in KB.",
	)
	parser.add_argument(
		"--l2-size",
		type=int,
		default=DEFAULT_L2_SIZE,
		help="L2 cache size in KB.",
	)
	parser.add_argument(
		"--l3-size",
		type=int,
		default=DEFAULT_L3_SIZE,
		help="L3 cache size in KB.",
	)
	parser.add_argument(
		"--l1i-assoc",
		type=int,
		default=DEFAULT_L1I_ASSOC,
		help="L1 instruction cache associativity.",
	)
	parser.add_argument(
		"--l1d-assoc",
		type=int,
		default=DEFAULT_L1D_ASSOC,
		help="L1 data cache associativity.",
	)
	parser.add_argument(
		"--l2-assoc",
		type=int,
		default=DEFAULT_L2_ASSOC,
		help="L2 cache associativity.",
	)
	parser.add_argument(
		"--l3-assoc",
		type=int,
		default=DEFAULT_L3_ASSOC,
		help="L3 cache associativity.",
	)
	parser.add_argument(
		"--branch-predictor-type",
		default=DEFAULT_BRANCH_PREDICTOR_TYPE,
		help="Branch predictor type for perf_model/branch_predictor/type.",
	)
	parser.add_argument(
		"--branch-mispredict-penalty",
		type=int,
		default=DEFAULT_BRANCH_MISPREDICT_PENALTY,
		help="Branch mispredict penalty in cycles.",
	)
	parser.add_argument(
		"--branch-predictor-size",
		type=int,
		default=DEFAULT_BRANCH_PREDICTOR_SIZE,
		help="Branch predictor table size.",
	)
	parser.add_argument(
		"--set",
		action="append",
		default=[],
		help="Raw config override in the form section/key=value. Repeatable.",
	)
	parser.add_argument(
		"--set-list",
		action="append",
		default=[],
		help="Raw list override in the form section/key=a,b,c. Repeatable.",
	)
	parser.add_argument(
		"--show-config",
		action="store_true",
		help="Print the resolved Sniper command and exit without running it.",
	)
	parser.add_argument("cmd", nargs=argparse.REMAINDER, help="Command to run after --.")
	return parser


def main() -> int:
	parser = build_parser()
	args = parser.parse_args()

	sniper = Path(args.sniper).expanduser().resolve()
	if not sniper.exists():
		parser.error(f"run-sniper not found: {sniper}")

	forwarded: list[str] = [str(sniper), "-n", str(args.cores), "-d", str(Path(args.outputdir).expanduser())]
	add_cfg_arg(forwarded, "general/total_cores", args.cores)
	add_cfg_arg_if_changed(forwarded, "perf_model/core/core_model", args.core_model, DEFAULT_CORE_MODEL)
	add_cfg_arg_if_changed(forwarded, "perf_model/core/type", args.core_type, DEFAULT_CORE_TYPE)
	add_cfg_arg_if_changed(forwarded, "perf_model/core/frequency", args.frequency, DEFAULT_FREQUENCY)
	add_cfg_arg_if_changed(forwarded, "perf_model/core/logical_cpus", args.logical_cpus, DEFAULT_LOGICAL_CPUS)
	add_cfg_arg_if_changed(forwarded, "perf_model/l1_icache/cache_size", args.l1i_size, DEFAULT_L1I_SIZE)
	add_cfg_arg_if_changed(forwarded, "perf_model/l1_dcache/cache_size", args.l1d_size, DEFAULT_L1D_SIZE)
	add_cfg_arg_if_changed(forwarded, "perf_model/l2_cache/cache_size", args.l2_size, DEFAULT_L2_SIZE)
	add_cfg_arg_if_changed(forwarded, "perf_model/l3_cache/cache_size", args.l3_size, DEFAULT_L3_SIZE)
	add_cfg_arg_if_changed(forwarded, "perf_model/l1_icache/associativity", args.l1i_assoc, DEFAULT_L1I_ASSOC)
	add_cfg_arg_if_changed(forwarded, "perf_model/l1_dcache/associativity", args.l1d_assoc, DEFAULT_L1D_ASSOC)
	add_cfg_arg_if_changed(forwarded, "perf_model/l2_cache/associativity", args.l2_assoc, DEFAULT_L2_ASSOC)
	add_cfg_arg_if_changed(forwarded, "perf_model/l3_cache/associativity", args.l3_assoc, DEFAULT_L3_ASSOC)
	add_cfg_arg_if_changed(forwarded, "perf_model/branch_predictor/type", args.branch_predictor_type, DEFAULT_BRANCH_PREDICTOR_TYPE)
	add_cfg_arg_if_changed(forwarded, "perf_model/branch_predictor/mispredict_penalty", args.branch_mispredict_penalty, DEFAULT_BRANCH_MISPREDICT_PENALTY)
	add_cfg_arg_if_changed(forwarded, "perf_model/branch_predictor/size", args.branch_predictor_size, DEFAULT_BRANCH_PREDICTOR_SIZE)
	forwarded.extend(["-c", DEFAULT_CONFIG])

	if args.cmd[:1] == ["--"]:
		forwarded.extend(args.cmd[1:])
	else:
		forwarded.extend(args.cmd)

	if args.show_config:
		print("Resolved Sniper command:")
		print(" ".join(forwarded))
		return 0

	env = os.environ.copy()
	env.setdefault("SNIPER_ROOT", str(ROOT / "snipersim"))

	os.execvpe(sys.executable, [sys.executable, *forwarded], env)
	return 1


if __name__ == "__main__":
	raise SystemExit(main())
