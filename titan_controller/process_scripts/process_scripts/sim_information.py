from abc import ABC, abstractmethod
from typing import List
from dataclasses import dataclass
from pathlib import Path
import pandas as pd

@dataclass
class SimData:
    source: str
    section: str
    key: str
    core: int | None
    value: object

class SimulatorInformation(ABC):
    @abstractmethod
    def __init__(self, dir_path: Path):
        pass

    @abstractmethod
    def get(self) -> List[SimData]:
        pass
