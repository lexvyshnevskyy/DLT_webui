from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

# Legacy Delphi checkbox frequencies (Hz).
STANDARD_FREQUENCIES: List[int] = [
    25, 50, 60, 100, 120, 200, 500, 1000, 2000, 5000, 10000, 20000,
    50000, 100000, 200000, 500000, 1000000,
]

SWEEP_MODE_LABELS = {
    0: 'Stationary (hold current frequency)',
    1: 'Cyclic (rotate enabled frequencies)',
    2: 'Discrete (jump on enabled frequencies)',
    3: 'Range (min checkbox → max Hz field)',
}


@dataclass
class E720SweepConfig:
    mode: int = 0
    enabled_frequencies: Set[int] = field(default_factory=lambda: {1000})
    range_min_hz: float = 1000.0
    range_max_hz: float = 10000.0
    log_every_n_ticks: int = 1
    trigger_byte: int = 1

    def to_db_payload(self, program_id: int) -> Dict[str, Any]:
        return {
            'id': program_id,
            'param': int(self.mode),
            'config': {
                'mode': int(self.mode),
                'enabled_frequencies': sorted(self.enabled_frequencies),
                'range_min_hz': float(self.range_min_hz),
                'range_max_hz': float(self.range_max_hz),
                'log_every_n_ticks': int(self.log_every_n_ticks),
                'trigger_byte': int(self.trigger_byte),
            },
        }

    @classmethod
    def from_db_row(cls, row: Dict[str, Any]) -> 'E720SweepConfig':
        cfg = E720SweepConfig(mode=int(row.get('param', 0) or 0))
        raw = row.get('config')
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = {}
        if isinstance(raw, dict):
            cfg.enabled_frequencies = set(int(x) for x in raw.get('enabled_frequencies', []) or STANDARD_FREQUENCIES[:1])
            cfg.range_min_hz = float(raw.get('range_min_hz', cfg.range_min_hz))
            cfg.range_max_hz = float(raw.get('range_max_hz', cfg.range_max_hz))
            cfg.log_every_n_ticks = max(1, int(raw.get('log_every_n_ticks', 1)))
            cfg.trigger_byte = int(raw.get('trigger_byte', 1))
        return cfg


class E720SweepController:
    """Legacy-style E7-20 frequency handling during an experiment."""

    def __init__(self) -> None:
        self.config = E720SweepConfig()
        self._tick_counter = 0
        self._cyclic_index = 0
        self._last_frequency: Optional[float] = None

    def load_config(self, config: E720SweepConfig) -> None:
        self.config = config
        self.reset()

    def reset(self) -> None:
        self._tick_counter = 0
        self._cyclic_index = 0
        self._last_frequency = None

    def tick(self, current_frequency: float, running: bool) -> Dict[str, Any]:
        """Return optional command byte to send and whether to log this tick."""
        self._tick_counter += 1
        log_now = (self._tick_counter % max(1, self.config.log_every_n_ticks)) == 0
        if not running:
            return {'log': False, 'command_byte': None}

        mode = int(self.config.mode)
        cmd: Optional[int] = None

        if mode == 0:
            # Stationary: periodic trigger to refresh reading.
            if log_now:
                cmd = self.config.trigger_byte
        elif mode == 1:
            enabled = sorted(self.config.enabled_frequencies) or STANDARD_FREQUENCIES[:1]
            if log_now:
                self._cyclic_index = (self._cyclic_index + 1) % len(enabled)
                cmd = self.config.trigger_byte
        elif mode == 2:
            enabled = sorted(self.config.enabled_frequencies)
            nearest = min(enabled, key=lambda f: abs(f - current_frequency)) if enabled else None
            if nearest is not None and abs(nearest - current_frequency) < max(1.0, nearest * 0.05):
                if log_now:
                    cmd = self.config.trigger_byte
        elif mode == 3:
            if self.config.range_min_hz <= current_frequency <= self.config.range_max_hz:
                if log_now:
                    cmd = self.config.trigger_byte
            else:
                cmd = self.config.trigger_byte

        self._last_frequency = current_frequency
        return {'log': log_now, 'command_byte': cmd, 'mode': mode}
