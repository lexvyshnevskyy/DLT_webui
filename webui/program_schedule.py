"""DB program step parsing for webui (scheduling runs in core only)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class ProgramStep:
    step_id: int
    t_start: float
    t_stop: float
    minutes: float


IDLE_EXPERIMENT_TIMING: Dict[str, Any] = {
    'active': False,
    'elapsed_s': 0.0,
    'remaining_s': 0.0,
    'total_s': 0.0,
    'elapsed_text': '0:00',
    'remaining_text': '0:00',
    'total_text': '0:00',
    'progress_percent': 0.0,
}


def parse_program_steps(rows: List[str]) -> List[ProgramStep]:
    steps: List[ProgramStep] = []
    for raw_row in rows:
        parts = str(raw_row).split('^')
        if len(parts) < 4:
            continue
        steps.append(
            ProgramStep(
                step_id=int(parts[0]),
                t_start=float(parts[1]),
                t_stop=float(parts[2]),
                minutes=float(parts[3]),
            )
        )
    steps.sort(key=lambda item: item.step_id)
    return steps
