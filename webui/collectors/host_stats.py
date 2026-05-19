from __future__ import annotations

import os
from typing import Any, Dict, List

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore


def collect_host_stats() -> Dict[str, Any]:
    if psutil is None:
        return {
            'available': False,
            'error': 'psutil is not installed',
        }

    cpu_percent = psutil.cpu_percent(interval=None)
    try:
        load_avg = list(os.getloadavg())
    except (AttributeError, OSError):
        load_avg = []

    memory = psutil.virtual_memory()
    disk_rows: List[List[Any]] = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        disk_rows.append([
            part.device,
            part.mountpoint,
            part.fstype,
            round(usage.total / (1024 ** 3), 2),
            round(usage.used / (1024 ** 3), 2),
            round(usage.free / (1024 ** 3), 2),
            round(usage.percent, 1),
        ])

    return {
        'available': True,
        'cpu_percent': round(cpu_percent, 1),
        'load_avg': [round(v, 2) for v in load_avg],
        'memory_percent': round(memory.percent, 1),
        'memory_used_gb': round(memory.used / (1024 ** 3), 2),
        'memory_total_gb': round(memory.total / (1024 ** 3), 2),
        'disk_rows': disk_rows,
    }
