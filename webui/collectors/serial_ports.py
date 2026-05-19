from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional


ENV_KEYS = {
    'LTM2985': 'DELATOMETRY_LTM2985_PORT',
    'E7-20 / measure_device': 'DELATOMETRY_MEASURE_PORT',
    'HMI Nextion': 'DELATOMETRY_HMI_PORT',
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
            row['device'],
            'yes' if row['exists'] else 'no',
            'yes' if row.get('readable') else 'no',
            'yes' if row.get('writable') else 'no',
        ]
        for row in collect_uart_status(env_file)
    ]


def list_serial_devices() -> List[str]:
    candidates: List[str] = []
    for pattern in ('/dev/ttyUSB*', '/dev/ttyACM*', '/dev/ttyAMA*', '/dev/ttyS*'):
        candidates.extend(str(p) for p in Path('/dev').glob(pattern.name))
    return sorted(set(candidates))
