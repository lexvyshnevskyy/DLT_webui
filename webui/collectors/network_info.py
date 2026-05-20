from __future__ import annotations

import re
import subprocess
from typing import Any, Dict, List, Optional

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore

_IPV4_RE = re.compile(r'^(\d+\.\d+\.\d+\.\d+)/(\d+)$')


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
        ipv4_list: List[str] = []
        ipv6_list: List[str] = []
        mac = ''
        for addr in addr_list:
            fam = addr.family.name
            if fam == 'AF_INET':
                prefix = _netmask_to_prefix(addr.netmask)
                ipv4_list.append(f'{addr.address}/{prefix}' if prefix else addr.address)
            elif fam == 'AF_INET6':
                ipv6_list.append(addr.address)
            elif fam == 'AF_PACKET' and addr.address:
                mac = addr.address
        stat = stats.get(name)
        kind = 'wifi' if name.startswith('wlan') or name.startswith('wl') else 'ethernet'
        rows.append({
            'interface': name,
            'kind': kind,
            'up': bool(stat.isup) if stat else False,
            'mac': mac,
            'ipv4': ', '.join(ipv4_list) or '-',
            'ipv6': ', '.join(ipv6_list[:2]) or '-',
        })
    return rows


def _netmask_to_prefix(netmask: str) -> int:
    try:
        parts = [int(x) for x in netmask.split('.')]
        bits = ''.join(f'{octet:08b}' for octet in parts)
        return bits.count('1')
    except (ValueError, AttributeError):
        return 24


def get_interface(name: str) -> Optional[Dict[str, Any]]:
    for row in collect_interfaces():
        if row['interface'] == name:
            return row
    return None


def list_manageable_interfaces() -> List[str]:
    names = [r['interface'] for r in collect_interfaces() if r['kind'] in {'ethernet', 'wifi'}]
    preferred = [n for n in ('eth0', 'wlan0') if n in names]
    for n in names:
        if n not in preferred:
            preferred.append(n)
    return preferred


def interfaces_table() -> List[List[Any]]:
    return [
        [r['interface'], 'up' if r['up'] else 'down', r['mac'], r['ipv4']]
        for r in collect_interfaces()
    ]


def parse_primary_ipv4(iface: str) -> Dict[str, Any]:
    """Split first IPv4 on interface into address + prefix for form fields."""
    row = get_interface(iface)
    if not row:
        return {'address': '', 'prefix': 24, 'gateway': '', 'dns': ''}
    ipv4_field = str(row.get('ipv4', '')).split(',')[0].strip()
    if ipv4_field in ('', '-'):
        return {'address': '', 'prefix': 24, 'gateway': '', 'dns': ''}
    match = _IPV4_RE.match(ipv4_field)
    if match:
        return {'address': match.group(1), 'prefix': int(match.group(2)), 'gateway': '', 'dns': ''}
    return {'address': ipv4_field, 'prefix': 24, 'gateway': '', 'dns': ''}


def nmcli_available() -> bool:
    return _run(['which', 'nmcli']).returncode == 0


def list_nmcli_connections() -> List[str]:
    """Deprecated: profile names are no longer used in the web UI."""
    if not nmcli_available():
        return []
    result = _run(['nmcli', '-t', '-f', 'NAME', 'con', 'show'])
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]
