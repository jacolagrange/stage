"""Shared checkpoint/serialization primitives used by every search
strategy's resumable-state dataclass -- see README's "Resumability" section."""
import dataclasses
import json
import shutil
from pathlib import Path

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
