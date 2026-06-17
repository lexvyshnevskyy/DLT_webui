from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


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


def _resolve_symlink(device: str) -> str:
    try:
        return os.path.realpath(device)
    except OSError:
        return ''


def _tty_sort_key(device: str) -> Tuple[int, int, str]:
    if '/by-id/' in device:
        return (0, 0, device)
    m = re.search(r'ttyUSB(\d+)$', device)
    if m:
        return (1, int(m.group(1)), device)
    m = re.search(r'ttyACM(\d+)$', device)
    if m:
        return (2, int(m.group(1)), device)
    return (3, 0, device)


def list_serial_port_options() -> List[Dict[str, str]]:
    """Enumerate serial ports with labels for configuration dropdowns."""
    options: Dict[str, Dict[str, str]] = {}

    try:
        from serial.tools import list_ports

        for info in list_ports.comports(include_links=True):
            device = (info.device or '').strip()
            if not device:
                continue
            parts: List[str] = []
            if info.description:
                parts.append(info.description.strip())
            if info.manufacturer:
                parts.append(info.manufacturer.strip())
            if info.serial_number:
                parts.append(f'S/N {info.serial_number.strip()}')
            target = _resolve_symlink(device)
            label = device
            if parts:
                label = f'{device} — {" · ".join(parts)}'
            if target and target != device:
                label = f'{label} → {target}'
            options[device] = {'device': device, 'label': label}
    except ImportError:
        pass

    by_id = Path('/dev/serial/by-id')
    if by_id.is_dir():
        for entry in sorted(by_id.iterdir()):
            if not entry.is_symlink():
                continue
            device = str(entry)
            if device in options:
                continue
            name = entry.name.replace('_', ' ')
            target = _resolve_symlink(device)
            label = f'{name} → {target}' if target else name
            options[device] = {'device': device, 'label': label}

    by_path = Path('/dev/serial/by-path')
    if by_path.is_dir():
        for entry in sorted(by_path.iterdir()):
            if not entry.is_symlink():
                continue
            device = str(entry)
            if device in options:
                continue
            target = _resolve_symlink(device)
            label = f'{entry.name} → {target}' if target else entry.name
            options[device] = {'device': device, 'label': label}

    dev = Path('/dev')
    for glob_pattern in ('ttyUSB*', 'ttyACM*', 'ttyAMA*'):
        for path in sorted(dev.glob(glob_pattern)):
            device = str(path)
            if device in options:
                continue
            options[device] = {'device': device, 'label': device}

    for i in range(4):
        device = f'/dev/ttyS{i}'
        if Path(device).exists() and device not in options:
            options[device] = {'device': device, 'label': device}

    return sorted(options.values(), key=lambda row: _tty_sort_key(row['device']))


def list_serial_devices() -> List[str]:
    return [row['device'] for row in list_serial_port_options()]


def serial_port_inventory(env_file: str) -> List[Dict[str, Any]]:
    """All detected ports plus configured role assignments."""
    env = _read_env_file(env_file)
    assignments: Dict[str, List[str]] = {}
    for label, key in ENV_KEYS.items():
        port = env.get(key, '').strip()
        if not port:
            continue
        assignments.setdefault(port, []).append(label)
        resolved = _resolve_symlink(port)
        if resolved and resolved != port:
            assignments.setdefault(resolved, []).append(label)

    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for opt in list_serial_port_options():
        device = opt['device']
        seen.add(device)
        resolved = _resolve_symlink(device)
        roles = list(dict.fromkeys(assignments.get(device, []) + assignments.get(resolved, [])))
        status = _device_status(resolved or device)
        rows.append({
            'device': device,
            'label': opt['label'],
            'resolved': resolved or device,
            'roles': roles,
            'ok': status['ok'],
            'status': 'OK' if status['ok'] else ('missing' if not status['exists'] else 'no access'),
        })

    for port, roles in sorted(assignments.items()):
        if port in seen:
            continue
        status = _device_status(port)
        rows.append({
            'device': port,
            'label': f'{port} (configured, not detected)',
            'resolved': _resolve_symlink(port) or port,
            'roles': roles,
            'ok': status['ok'],
            'status': 'OK' if status['ok'] else ('missing' if not status['exists'] else 'no access'),
        })

    return rows


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
