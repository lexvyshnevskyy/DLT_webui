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
        if part.fstype in ('squashfs', 'tmpfs', 'devtmpfs', 'proc', 'sysfs', 'devpts', 'cgroup2'):
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
            stat = os.statvfs(part.mountpoint)
        except (PermissionError, OSError):
            continue
        inode_total = int(stat.f_files)
        inode_free = int(stat.f_ffree)
        inode_free_pct = round(100.0 * inode_free / inode_total, 1) if inode_total > 0 else 0.0
        disk_label = part.mountpoint if part.mountpoint else part.device
        if part.device and part.mountpoint and part.device != part.mountpoint:
            disk_label = f'{part.device} ({part.mountpoint})'
        disk_rows.append([
            disk_label,
            round(usage.percent, 1),
            inode_free_pct,
            round(usage.free / (1024 ** 3), 2),
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
