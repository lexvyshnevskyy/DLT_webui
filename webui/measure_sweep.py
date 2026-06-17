from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from webui.measure_source import normalize_measure_source

# Legacy Delphi E7-20 checkbox frequencies (Hz).
E720_STANDARD_FREQUENCIES: List[int] = [
    25, 50, 60, 100, 120, 200, 500, 1000, 2000, 5000, 10000, 20000,
    50000, 100000, 200000, 500000, 1000000,
]

# Hioki IM3536: DC/4 Hz – 8 MHz (common preset points for UI).
IM3536_STANDARD_FREQUENCIES: List[int] = [
    4, 10, 40, 100, 120, 200, 500, 1000, 2000, 5000, 10000, 20000,
    50000, 100000, 200000, 500000, 1000000, 2000000, 5000000, 8000000,
]

STANDARD_FREQUENCIES = E720_STANDARD_FREQUENCIES

SWEEP_MODE_LABELS = {
    0: 'Stationary (hold current frequency)',
    1: 'Cyclic (rotate enabled frequencies)',
    2: 'Discrete (jump on enabled frequencies)',
    3: 'Range (min checkbox → max Hz field)',
}


def standard_frequencies_for_device(device: str) -> List[int]:
    if normalize_measure_source(device) == 'im3536':
        return list(IM3536_STANDARD_FREQUENCIES)
    return list(E720_STANDARD_FREQUENCIES)


@dataclass
class MeasureSweepConfig:
    device: str = 'e720'
    mode: int = 0
    enabled_frequencies: Set[int] = field(default_factory=lambda: {1000})
    range_min_hz: float = 1000.0
    range_max_hz: float = 10000.0
    log_every_n_ticks: int = 1
    trigger_byte: int = 1

    def normalized_device(self) -> str:
        return normalize_measure_source(self.device)

    def to_db_payload(self, program_id: int) -> Dict[str, Any]:
        return {
            'id': program_id,
            'param': int(self.mode),
            'config': {
                'device': self.normalized_device(),
                'mode': int(self.mode),
                'enabled_frequencies': sorted(self.enabled_frequencies),
                'range_min_hz': float(self.range_min_hz),
                'range_max_hz': float(self.range_max_hz),
                'log_every_n_ticks': int(self.log_every_n_ticks),
                'trigger_byte': int(self.trigger_byte),
            },
        }

    @classmethod
    def from_db_row(cls, row: Dict[str, Any], *, fallback_device: str = 'e720') -> 'MeasureSweepConfig':
        cfg = cls(mode=int(row.get('param', 0) or 0), device=fallback_device)
        raw = row.get('config')
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = {}
        if isinstance(raw, dict):
            cfg.device = normalize_measure_source(str(raw.get('device', fallback_device)))
            freqs = raw.get('enabled_frequencies') or []
            default_freqs = standard_frequencies_for_device(cfg.device)[:1]
            cfg.enabled_frequencies = set(int(x) for x in freqs) or set(default_freqs)
            cfg.range_min_hz = float(raw.get('range_min_hz', cfg.range_min_hz))
            cfg.range_max_hz = float(raw.get('range_max_hz', cfg.range_max_hz))
            cfg.log_every_n_ticks = max(1, int(raw.get('log_every_n_ticks', 1)))
            cfg.trigger_byte = int(raw.get('trigger_byte', 1))
        return cfg

    @classmethod
    def for_device(
        cls,
        device: str,
        *,
        mode: int = 0,
        enabled_frequencies: Optional[Set[int]] = None,
        range_min_hz: float = 1000.0,
        range_max_hz: float = 10000.0,
    ) -> 'MeasureSweepConfig':
        dev = normalize_measure_source(device)
        freqs = enabled_frequencies or set(standard_frequencies_for_device(dev)[:1])
        return cls(
            device=dev,
            mode=int(mode),
            enabled_frequencies=set(freqs),
            range_min_hz=float(range_min_hz if range_min_hz else min(freqs)),
            range_max_hz=float(range_max_hz),
        )


E720SweepConfig = MeasureSweepConfig


class MeasureSweepController:
    """Frequency sweep during an experiment (E7-20 trigger bytes or IM3536 SCPI Hz)."""

    def __init__(self) -> None:
        self.config = MeasureSweepConfig()
        self._tick_counter = 0
        self._cyclic_index = 0
        self._discrete_index = 0
        self._last_frequency: Optional[float] = None
        self._initial_applied = False

    def load_config(self, config: MeasureSweepConfig) -> None:
        self.config = config
        self.reset()

    def reset(self) -> None:
        self._tick_counter = 0
        self._cyclic_index = 0
        self._discrete_index = 0
        self._last_frequency = None
        self._initial_applied = False

    def initial_target_frequency(self) -> Optional[float]:
        enabled = sorted(self.config.enabled_frequencies)
        if not enabled or self.config.normalized_device() != 'im3536':
            return None
        return float(enabled[0])

    def tick(self, current_frequency: float, running: bool) -> Dict[str, Any]:
        self._tick_counter += 1
        log_now = (self._tick_counter % max(1, self.config.log_every_n_ticks)) == 0
        if not running:
            return {'log': False, 'command_byte': None, 'target_frequency_hz': None}

        if self.config.normalized_device() == 'im3536':
            target = self._im3536_target_frequency(current_frequency, log_now)
            self._last_frequency = current_frequency
            return {
                'log': log_now,
                'command_byte': None,
                'target_frequency_hz': target,
                'mode': int(self.config.mode),
            }

        mode = int(self.config.mode)
        cmd: Optional[int] = None

        if mode == 0:
            if log_now:
                cmd = self.config.trigger_byte
        elif mode == 1:
            if log_now:
                self._cyclic_index = (self._cyclic_index + 1) % max(
                    1, len(sorted(self.config.enabled_frequencies) or E720_STANDARD_FREQUENCIES[:1])
                )
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
        return {'log': log_now, 'command_byte': cmd, 'target_frequency_hz': None, 'mode': mode}

    def _im3536_target_frequency(self, current_frequency: float, log_now: bool) -> Optional[float]:
        enabled = sorted(self.config.enabled_frequencies) or IM3536_STANDARD_FREQUENCIES[:1]
        mode = int(self.config.mode)

        if mode == 0:
            return float(enabled[0])

        if mode == 1:
            if not log_now:
                return None
            target = float(enabled[self._cyclic_index % len(enabled)])
            self._cyclic_index = (self._cyclic_index + 1) % len(enabled)
            return target

        if mode == 2:
            if not enabled:
                return None
            target = float(enabled[self._discrete_index % len(enabled)])
            if abs(current_frequency - target) <= max(1.0, target * 0.05):
                self._discrete_index = (self._discrete_index + 1) % len(enabled)
                return float(enabled[self._discrete_index % len(enabled)])
            return target

        if mode == 3:
            lo = float(min(enabled))
            hi = float(max(self.config.range_max_hz, max(enabled)))
            if current_frequency < lo:
                return lo
            if current_frequency > hi:
                return hi
            if log_now:
                return float(enabled[self._cyclic_index % len(enabled)])
            return None

        return float(enabled[0])


E720SweepController = MeasureSweepController
