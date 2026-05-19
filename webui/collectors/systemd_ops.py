from __future__ import annotations

import subprocess
from typing import Any, Dict, List, Optional


DEFAULT_UNITS = [
    'delatometry-database.service',
    'delatometry-ltm2985.service',
    'delatometry-measure-device.service',
    'delatometry-ads1256.service',
    'delatometry-core.service',
    'delatometry-hmi.service',
    'delatometry-webui.service',
    'mariadb.service',
    'pigpiod.service',
]


def _run(cmd: List[str], use_sudo: bool = False, timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    full_cmd = ['sudo', '-n', *cmd] if use_sudo else cmd
    return subprocess.run(
        full_cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def get_unit_status(unit: str) -> Dict[str, str]:
    result = _run(['systemctl', 'show', unit, '--property=ActiveState,SubState,UnitFileState,MainPID'], use_sudo=False)
    props: Dict[str, str] = {}
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                props[key.strip()] = value.strip()
    active = _run(['systemctl', 'is-active', unit], use_sudo=False)
    return {
        'unit': unit,
        'active': active.stdout.strip() if active.returncode == 0 else 'unknown',
        'active_state': props.get('ActiveState', ''),
        'sub_state': props.get('SubState', ''),
        'enabled': props.get('UnitFileState', ''),
        'pid': props.get('MainPID', ''),
        'error': result.stderr.strip() if result.returncode != 0 else '',
    }


def list_units(units: Optional[List[str]] = None) -> List[Dict[str, str]]:
    return [get_unit_status(unit) for unit in (units or DEFAULT_UNITS)]


def units_table(units: Optional[List[str]] = None) -> List[List[Any]]:
    rows: List[List[Any]] = []
    for item in list_units(units):
        rows.append([
            item['unit'],
            item['active'],
            item['sub_state'],
            item['enabled'],
            item['pid'],
        ])
    return rows


def control_unit(unit: str, action: str, use_sudo: bool = True) -> Dict[str, Any]:
    action = action.strip().lower()
    if action not in {'start', 'stop', 'restart'}:
        return {'ok': False, 'error': f'Unsupported action: {action}'}
    if not unit.endswith('.service'):
        unit = f'{unit}.service'
    result = _run(['systemctl', action, unit], use_sudo=use_sudo)
    ok = result.returncode == 0
    return {
        'ok': ok,
        'unit': unit,
        'action': action,
        'stdout': result.stdout.strip(),
        'stderr': result.stderr.strip(),
        'error': '' if ok else (result.stderr.strip() or result.stdout.strip() or f'exit {result.returncode}'),
    }
