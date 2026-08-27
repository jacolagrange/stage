"""Shared checkpoint/serialization primitives used by every search
strategy's resumable-state dataclass"""
import dataclasses
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from .config import PARAM_SPACE
from .models import DesignPoint


def state_path(outputdir: Path) -> Path:
    return outputdir / "search_state.json"


def point_to_dict(p: DesignPoint) -> dict:
    d = dataclasses.asdict(p)
    d["modified_params"] = sorted(d["modified_params"])
    d["output_path"] = str(d["output_path"]) if d["output_path"] else None
    return d


def point_from_dict(d: dict) -> DesignPoint:
    d = dict(d)
    d["modified_params"] = set(d["modified_params"])
    d["output_path"] = Path(d["output_path"]) if d["output_path"] else None
    return DesignPoint(**d)


def write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def read_raw_state(outputdir: Path) -> dict | None:
    path = state_path(outputdir)
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def rng_state_to_json(rng) -> list:
    version, internal_state, gauss_next = rng.getstate()
    return [version, list(internal_state), gauss_next]


def rng_state_from_json(data: list) -> tuple:
    version, internal_state, gauss_next = data
    return (version, tuple(internal_state), gauss_next)


def cleanup_dirs(dirs: set[Path]) -> int:
    count = 0
    for d in dirs:
        if d and d.exists():
            shutil.rmtree(d, ignore_errors=True)
            count += 1
    return count


@dataclass
class SearchStateBase:
    """Common resumable-state contract shared by every search strategy's
    checkpoint dataclass (GreedySearchState, MesmoSearchState,
    Spea2SearchState): identity check against the run's config/benchmarks/
    alpha/param-space, and save/load through state_path()'s JSON file.
    Subclasses set STRATEGY and implement to_dict()/from_dict() for their
    own (differing) set of fields."""
    STRATEGY: ClassVar[str] = ""

    reference_config: str
    benchmarks: dict[str, list[str]]
    alpha: float
    param_space: dict[str, list]

    def matches(self, reference_config: str, benchmarks: dict[str, list[str]], alpha: float) -> bool:
        return (
            self.reference_config == str(reference_config)
            and self.benchmarks == benchmarks
            and self.alpha == alpha
            and self.param_space == self._current_param_space()
        )

    @staticmethod
    def _current_param_space() -> dict[str, list]:
        """Live PARAM_SPACE to compare a loaded checkpoint's param_space
        against. Overridden per strategy module (each returns that module's
        own PARAM_SPACE binding) so cli.py's pre-evaluation-screening
        PARAM_SPACE monkeypatch (greedy.PARAM_SPACE = pruned_param_space,
        etc.) is respected instead of always reading config.py's original."""
        return PARAM_SPACE

    def to_dict(self) -> dict:
        raise NotImplementedError

    @classmethod
    def from_dict(cls, d: dict) -> "SearchStateBase":
        raise NotImplementedError

    def save(self, outputdir: Path) -> None:
        write_json_atomic(state_path(outputdir), self.to_dict())

    @classmethod
    def load(cls, outputdir: Path) -> "SearchStateBase | None":
        raw = read_raw_state(outputdir)
        if raw is None:
            return None
        found = raw.get("strategy", "greedy")
        if found != cls.STRATEGY:
            print(f"Saved search state at {state_path(outputdir)} was written by strategy "
                  f"'{found}', not '{cls.STRATEGY}' — starting fresh.\n")
            return None
        return cls.from_dict(raw)
