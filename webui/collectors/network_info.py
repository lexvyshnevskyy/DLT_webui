from __future__ import annotations

import subprocess
from typing import Any, Dict, List

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore


def _run(cmd: List[str], timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def collect_interfaces() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if psutil is None:
        return rows

    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    for name, addr_list in sorted(addrs.items()):
        if name == 'lo':
            continue
        ipv4 = []
        ipv6 = []
        mac = ''
        for addr in addr_list:
            if addr.family.name == 'AF_INET':
                ipv4.append(f'{addr.address}/{addr.netmask}')
            elif addr.family.name == 'AF_INET6':
                ipv6.append(addr.address)
            elif addr.family.name == 'AF_PACKET' and addr.address:
                mac = addr.address
        stat = stats.get(name)
        rows.append({
            'interface': name,
            'up': bool(stat.isup) if stat else False,
            'speed_mbps': getattr(stat, 'speed', 0) if stat else 0,
            'mac': mac,
            'ipv4': ', '.join(ipv4) or '-',
            'ipv6': ', '.join(ipv6[:2]) or '-',
        })
    return rows


def interfaces_table() -> List[List[Any]]:
    return [
        [r['interface'], 'up' if r['up'] else 'down', r['mac'], r['ipv4'], r['ipv6'], r['speed_mbps']]
        for r in collect_interfaces()
    ]


def nmcli_available() -> bool:
    return _run(['which', 'nmcli']).returncode == 0


def list_nmcli_connections() -> List[str]:
    if not nmcli_available():
        return []
    result = _run(['nmcli', '-t', '-f', 'NAME', 'con', 'show'])
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]
