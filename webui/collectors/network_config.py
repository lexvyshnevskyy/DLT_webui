from __future__ import annotations

import subprocess
from typing import Any, Dict, List

from .network_info import _run, nmcli_available


def wifi_scan() -> Dict[str, Any]:
    if not nmcli_available():
        return {'ok': False, 'error': 'nmcli is not available', 'rows': []}
    result = _run(['nmcli', '-t', '-f', 'IN-USE,SSID,SIGNAL,SECURITY', 'dev', 'wifi', 'list'])
    if result.returncode != 0:
        return {'ok': False, 'error': result.stderr.strip() or result.stdout.strip(), 'rows': []}
    rows: List[List[Any]] = []
    for line in result.stdout.splitlines():
        parts = line.split(':')
        if len(parts) < 4:
            continue
        rows.append([parts[0], parts[1], parts[2], parts[3]])
    return {'ok': True, 'error': '', 'rows': rows}


def wifi_connect(ssid: str, password: str, interface: str = 'wlan0', use_sudo: bool = True) -> Dict[str, Any]:
    if not nmcli_available():
        return {'ok': False, 'error': 'nmcli is not available'}
    ssid = ssid.strip()
    if not ssid:
        return {'ok': False, 'error': 'SSID is required'}
    cmd = ['nmcli', 'dev', 'wifi', 'connect', ssid, 'ifname', interface]
    if password.strip():
        cmd.extend(['password', password.strip()])
    full_cmd = ['sudo', '-n', *cmd] if use_sudo else cmd
    result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=60, check=False)
    return {
        'ok': result.returncode == 0,
        'stdout': result.stdout.strip(),
        'stderr': result.stderr.strip(),
        'error': '' if result.returncode == 0 else (result.stderr.strip() or result.stdout.strip()),
    }


def set_ipv4(
    connection: str,
    address: str,
    prefix: int,
    gateway: str = '',
    dns: str = '',
    use_sudo: bool = True,
) -> Dict[str, Any]:
    if not nmcli_available():
        return {'ok': False, 'error': 'nmcli is not available'}
    connection = connection.strip()
    if not connection:
        return {'ok': False, 'error': 'Connection name is required'}
    cidr = f'{address}/{int(prefix)}'
    cmd = [
        'nmcli', 'con', 'mod', connection,
        'ipv4.addresses', cidr,
        'ipv4.method', 'manual',
    ]
    if gateway.strip():
        cmd.extend(['ipv4.gateway', gateway.strip()])
    if dns.strip():
        cmd.extend(['ipv4.dns', dns.strip()])
    mod = subprocess.run(
        (['sudo', '-n', *cmd] if use_sudo else cmd),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if mod.returncode != 0:
        return {'ok': False, 'error': mod.stderr.strip() or mod.stdout.strip()}
    up = subprocess.run(
        (['sudo', '-n', 'nmcli', 'con', 'up', connection] if use_sudo else ['nmcli', 'con', 'up', connection]),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    ok = up.returncode == 0
    return {
        'ok': ok,
        'error': '' if ok else (up.stderr.strip() or up.stdout.strip()),
        'address': cidr,
    }


def set_dhcp(connection: str, use_sudo: bool = True) -> Dict[str, Any]:
    if not nmcli_available():
        return {'ok': False, 'error': 'nmcli is not available'}
    cmd = ['nmcli', 'con', 'mod', connection.strip(), 'ipv4.method', 'auto']
    result = subprocess.run(
        (['sudo', '-n', *cmd] if use_sudo else cmd),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        return {'ok': False, 'error': result.stderr.strip() or result.stdout.strip()}
    up = subprocess.run(
        (['sudo', '-n', 'nmcli', 'con', 'up', connection.strip()] if use_sudo else ['nmcli', 'con', 'up', connection.strip()]),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    ok = up.returncode == 0
    return {'ok': ok, 'error': '' if ok else (up.stderr.strip() or up.stdout.strip())}
