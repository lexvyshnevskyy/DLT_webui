from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ProgramStep:
    step_id: int
    t_start: float
    t_stop: float
    minutes: float


@dataclass
class ExperimentState:
    program_id: Optional[int] = None
    steps: List[ProgramStep] = field(default_factory=list)
    step_index: int = 0
    step_started_monotonic: Optional[float] = None
    status: str = 'Idle'
    last_target_k: Optional[float] = None


class ExperimentRunner:
    """Temperature program scheduler (step ramp) for webui."""

    @staticmethod
    def interpolate_target(step: ProgramStep, elapsed_s: float) -> float:
        duration_s = max(0.0, float(step.minutes) * 60.0)
        if duration_s <= 0.0:
            return float(step.t_stop)
        alpha = min(max(elapsed_s / duration_s, 0.0), 1.0)
        return float(step.t_start) + (float(step.t_stop) - float(step.t_start)) * alpha

    def tick(self, state: ExperimentState) -> Dict[str, Any]:
        """Advance scheduler one tick. Returns action dict for the node."""
        if state.program_id is None or not state.steps:
            return {'active': False}

        if state.step_index >= len(state.steps):
            return {'active': False, 'finished': True, 'program_id': state.program_id}

        step = state.steps[state.step_index]
        if state.step_started_monotonic is None:
            state.step_started_monotonic = time.monotonic()
            return {
                'active': True,
                'program_id': state.program_id,
                'target_k': float(step.t_start),
                'step_started': True,
                'step_index': state.step_index,
                'step_count': len(state.steps),
            }

        elapsed_s = max(0.0, time.monotonic() - float(state.step_started_monotonic))
        target_k = self.interpolate_target(step, elapsed_s)
        state.last_target_k = target_k
        state.status = f'Running step {state.step_index + 1}/{len(state.steps)}'

        duration_s = max(0.0, float(step.minutes) * 60.0)
        if elapsed_s >= duration_s:
            state.step_index += 1
            state.step_started_monotonic = time.monotonic()
            if state.step_index >= len(state.steps):
                return {
                    'active': False,
                    'finished': True,
                    'program_id': state.program_id,
                    'target_k': target_k,
                }
            return {
                'active': True,
                'program_id': state.program_id,
                'advanced_step': True,
                'step_index': state.step_index,
                'step_count': len(state.steps),
            }

        return {
            'active': True,
            'program_id': state.program_id,
            'target_k': target_k,
            'step_index': state.step_index,
            'step_count': len(state.steps),
        }

    @staticmethod
    def parse_steps(rows: List[str]) -> List[ProgramStep]:
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
