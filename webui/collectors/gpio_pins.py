from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Set


# BCM pins commonly usable for PWM on Raspberry Pi (pigpio).
DEFAULT_BCM_PINS: List[int] = [
    2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27,
]


def _pins_in_use_sysfs() -> Set[int]:
    used: Set[int] = set()
    gpio_root = Path('/sys/class/gpio')
    if not gpio_root.is_dir():
        return used
    for entry in gpio_root.iterdir():
        name = entry.name
        if name.startswith('gpio') and name[4:].isdigit():
            used.add(int(name[4:]))
    return used


def list_available_bcm_pins() -> List[int]:
    """BCM pins suitable for PWM dropdowns (excludes pins already exported in sysfs)."""
    in_use = _pins_in_use_sysfs()
    available = [p for p in DEFAULT_BCM_PINS if p not in in_use]
    return available or list(DEFAULT_BCM_PINS)


def bcm_pin_choices(current: int = 0) -> List[str]:
    pins = list_available_bcm_pins()
    if current and int(current) not in pins:
        pins.insert(0, int(current))
    return [str(p) for p in sorted(set(pins))]


def is_service_enabled(unit: str) -> bool:
    svc = unit if unit.endswith('.service') else f'{unit}.service'
    proc = subprocess.run(
        ['systemctl', 'is-enabled', svc],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    state = (proc.stdout or proc.stderr or '').strip().lower()
    return state in {'enabled', 'static', 'indirect'}


def set_service_enabled(unit: str, enabled: bool, use_sudo: bool = True) -> tuple[bool, str]:
    svc = unit if unit.endswith('.service') else f'{unit}.service'
    action = 'enable' if enabled else 'disable'
    cmd = ['sudo', '-n', 'systemctl', action, svc] if use_sudo else ['systemctl', action, svc]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    if proc.returncode != 0:
        return False, proc.stderr.strip() or proc.stdout.strip() or f'failed to {action} {svc}'
    if not enabled:
        stop_cmd = ['sudo', '-n', 'systemctl', 'stop', svc] if use_sudo else ['systemctl', 'stop', svc]
        stop = subprocess.run(stop_cmd, capture_output=True, text=True, timeout=30, check=False)
        if stop.returncode != 0 and 'not running' not in (stop.stderr or '').lower():
            return True, f'disabled {svc} (stop: {stop.stderr.strip() or stop.stdout.strip()})'
        return True, f'disabled and stopped {svc}'
    start_cmd = ['sudo', '-n', 'systemctl', 'start', svc] if use_sudo else ['systemctl', 'start', svc]
    start = subprocess.run(start_cmd, capture_output=True, text=True, timeout=30, check=False)
    if start.returncode != 0:
        return True, f'enabled {svc} but start failed: {start.stderr.strip() or start.stdout.strip()}'
    return True, f'enabled and started {svc}'
