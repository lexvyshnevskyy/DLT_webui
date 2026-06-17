from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional


ENV_KEYS = {
    'LTM2985': 'DELATOMETRY_LTM2985_PORT',
    'E7-20 / measure_device': 'DELATOMETRY_MEASURE_PORT',
    'IM3536': 'DELATOMETRY_IM3536_PORT',
    'HMI (Nextion)': 'DELATOMETRY_HMI_PORT',
}


def _read_env_file(path: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    env_path = Path(path)
    if not env_path.is_file():
        return values
    for line in env_path.read_text(encoding='utf-8', errors='replace').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _device_status(device: str) -> Dict[str, Any]:
    path = Path(device)
    exists = path.exists()
    readable = os.access(device, os.R_OK) if exists else False
    writable = os.access(device, os.W_OK) if exists else False
    return {
        'device': device,
        'exists': exists,
        'readable': readable,
        'writable': writable,
        'ok': exists and readable and writable,
    }


def collect_uart_status(env_file: str) -> List[Dict[str, Any]]:
    env = _read_env_file(env_file)
    rows: List[Dict[str, Any]] = []
    for label, key in ENV_KEYS.items():
        device = env.get(key, '')
        status = _device_status(device) if device else {'device': '', 'exists': False, 'readable': False, 'writable': False, 'ok': False}
        rows.append({
            'label': label,
            'env_key': key,
            'device': device,
            **status,
        })
    return rows


def uart_table(env_file: str) -> List[List[Any]]:
    return [
        [
            row['label'],
            row['device'] or '—',
            'OK' if row.get('ok') else ('missing' if not row['exists'] else 'no access'),
        ]
        for row in collect_uart_status(env_file)
    ]


def list_serial_devices() -> List[str]:
    candidates: List[str] = []
    dev = Path('/dev')
    for glob_pattern in ('ttyUSB*', 'ttyACM*', 'ttyAMA*', 'ttyS*'):
        candidates.extend(str(p) for p in dev.glob(glob_pattern))
    by_id = Path('/dev/serial/by-id')
    if by_id.is_dir():
        for entry in sorted(by_id.iterdir()):
            if entry.is_symlink():
                candidates.append(str(entry))
    return sorted(set(candidates))
