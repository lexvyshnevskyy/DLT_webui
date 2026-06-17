from __future__ import annotations

import ipaddress
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from .network_info import _run, collect_interfaces, get_interface, nmcli_available

# Personal hotspot (hardcoded per product spec).
HOTSPOT_SSID = 'NTNCND_DLT'
HOTSPOT_IP_CIDR = '172.16.0.2/24'
HOTSPOT_DHCP_START = '172.16.0.3'
HOTSPOT_DHCP_END = '172.16.0.12'
HOTSPOT_LEASE_TIME = '12h'
HOTSPOT_CONN_PREFIX = 'delatometry-hotspot-'

DELATOMETRY_ETC = Path('/etc/delatometry')
HOTSPOT_DNSMASQ_CONF = DELATOMETRY_ETC / 'hotspot-dnsmasq.conf'
HOTSPOT_STATE_FILE = Path('/run/delatometry/hotspot.interface')
WIFI_RESTORE_FILE = DELATOMETRY_ETC / 'wifi-restore.json'
HOTSPOT_DHCP_SERVICE = 'delatometry-hotspot-dnsmasq.service'


def _sudo(cmd: List[str], use_sudo: bool = True, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    full = ['sudo', '-n', *cmd] if use_sudo else cmd
    return subprocess.run(full, capture_output=True, text=True, timeout=timeout, check=False)


def _write_file_sudo(path: Path, content: str, use_sudo: bool = True) -> None:
    parent = path.parent
    mkdir = _sudo(['mkdir', '-p', str(parent)], use_sudo=use_sudo, timeout=15)
    if mkdir.returncode != 0:
        raise OSError(mkdir.stderr.strip() or mkdir.stdout.strip() or f'cannot create {parent}')
    cmd = ['sudo', '-n', 'tee', str(path)] if use_sudo else ['tee', str(path)]
    proc = subprocess.run(
        cmd,
        input=content,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        raise OSError(proc.stderr.strip() or proc.stdout.strip() or f'cannot write {path}')


def _remove_file_sudo(path: Path, use_sudo: bool = True) -> None:
    _sudo(['rm', '-f', str(path)], use_sudo=use_sudo, timeout=15)


def _read_file_sudo(path: Path, use_sudo: bool = True) -> str:
    proc = _sudo(['cat', str(path)], use_sudo=use_sudo, timeout=15)
    if proc.returncode != 0:
        return ''
    return proc.stdout


def _connection_fields(name: str, secrets: bool = False, use_sudo: bool = True) -> Dict[str, str]:
    cmd = ['nmcli', '-t']
    if secrets:
        cmd.append('-s')
    cmd.extend(['connection', 'show', name])
    proc = _sudo(cmd, use_sudo=use_sudo, timeout=30)
    fields: Dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if ':' not in line:
            continue
        key, _, val = line.partition(':')
        fields[key] = val
    return fields


def _connection_type(iface: str) -> str:
    info = get_interface(iface)
    if info and info.get('kind') == 'wifi':
        return 'wifi'
    return 'ethernet'


def _connections_on_interface(iface: str) -> List[str]:
    result = _run(['nmcli', '-t', '-f', 'NAME,DEVICE', 'con', 'show'])
    if result.returncode != 0:
        return []
    names: List[str] = []
    for line in result.stdout.splitlines():
        if ':' not in line:
            continue
        name, _, device = line.partition(':')
        name = name.strip()
        device = device.strip()
        if not name:
            continue
        if device == iface:
            names.append(name)
            continue
        if not device:
            fields = _connection_fields(name, use_sudo=False)
            if fields.get('connection.interface-name') == iface:
                names.append(name)
    return names


def _delete_connections_on_interface(iface: str, use_sudo: bool = True) -> None:
    for name in _connections_on_interface(iface):
        if name:
            _sudo(['nmcli', 'connection', 'delete', name], use_sudo=use_sudo, timeout=30)


def _hotspot_connection_name(iface: str) -> str:
    return f'{HOTSPOT_CONN_PREFIX}{iface}'


def _list_hotspot_connection_names() -> List[str]:
    result = _run(['nmcli', '-t', '-f', 'NAME', 'con', 'show'])
    if result.returncode != 0:
        return []
    return [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip().startswith(HOTSPOT_CONN_PREFIX)
    ]


def _iface_from_hotspot_connection_name(name: str) -> str:
    if name.startswith(HOTSPOT_CONN_PREFIX):
        return name[len(HOTSPOT_CONN_PREFIX):]
    return ''


def _find_hotspot_iface() -> str:
    try:
        active = HOTSPOT_STATE_FILE.read_text(encoding='utf-8').strip()
        if active:
            return active
    except OSError:
        pass

    result = _run(['nmcli', '-t', '-f', 'NAME,DEVICE', 'con', 'show', '--active'])
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            if ':' not in line:
                continue
            name, _, device = line.partition(':')
            if not name.strip().startswith(HOTSPOT_CONN_PREFIX):
                continue
            dev = device.strip()
            if dev:
                return dev
            return _iface_from_hotspot_connection_name(name.strip())

    for name in _list_hotspot_connection_names():
        iface = _iface_from_hotspot_connection_name(name)
        if iface:
            return iface

    if HOTSPOT_DNSMASQ_CONF.exists():
        try:
            for line in HOTSPOT_DNSMASQ_CONF.read_text(encoding='utf-8').splitlines():
                if line.startswith('interface='):
                    return line.split('=', 1)[1].strip()
        except OSError:
            pass
    return ''


def _device_wifi_mode(iface: str) -> str:
    result = _run(['nmcli', '-t', '-f', '802-11-WIRELESS.MODE', 'dev', 'show', iface])
    if result.returncode != 0:
        return ''
    for line in result.stdout.splitlines():
        if ':' in line:
            return line.split(':', 1)[1].strip()
    return ''


def hotspot_is_active(iface: str = '') -> bool:
    active_iface = _find_hotspot_iface()
    if active_iface:
        return active_iface == iface if iface else True
    if iface:
        return _device_wifi_mode(iface) == 'ap'
    for row in collect_interfaces():
        if row.get('kind') == 'wifi' and _device_wifi_mode(row['interface']) == 'ap':
            return True
    return False


def _prepare_wifi_client_iface(iface: str, use_sudo: bool = True) -> None:
    _sudo(['nmcli', 'radio', 'wifi', 'on'], use_sudo=use_sudo, timeout=15)
    _sudo(['nmcli', 'device', 'set', iface, 'managed', 'yes'], use_sudo=use_sudo, timeout=15)


def _save_wifi_restore(
    iface: str,
    ssid: str,
    password: str,
    key_mgmt: str,
    profile_name: str,
    use_sudo: bool = True,
) -> None:
    snapshot = {
        'interface': iface,
        'ssid': ssid,
        'password': password,
        'key_mgmt': key_mgmt or 'wpa-psk',
        'profile_name': profile_name,
    }
    _write_file_sudo(WIFI_RESTORE_FILE, json.dumps(snapshot, indent=2) + '\n', use_sudo=use_sudo)
    _sudo(['chmod', '600', str(WIFI_RESTORE_FILE)], use_sudo=use_sudo, timeout=15)


def _snapshot_wifi_client(iface: str, use_sudo: bool = True) -> None:
    """Persist the current Wi-Fi client profile before hotspot replaces it."""
    for name in _connections_on_interface(iface):
        if name.startswith(HOTSPOT_CONN_PREFIX):
            continue
        fields = _connection_fields(name, secrets=True, use_sudo=use_sudo)
        if fields.get('connection.type') != '802-11-wireless':
            continue
        if fields.get('802-11-wireless.mode') == 'ap':
            continue
        ssid = fields.get('802-11-wireless.ssid', '').strip()
        if not ssid:
            continue
        _save_wifi_restore(
            iface,
            ssid,
            fields.get('802-11-wireless-security.psk', ''),
            fields.get('802-11-wireless-security.key-mgmt', 'wpa-psk'),
            name,
            use_sudo=use_sudo,
        )
        return


def _cleanup_hotspot_connections(use_sudo: bool = True) -> None:
    for name in _list_hotspot_connection_names():
        _sudo(['nmcli', 'connection', 'down', name], use_sudo=use_sudo, timeout=20)
        _sudo(['nmcli', 'connection', 'delete', name], use_sudo=use_sudo, timeout=20)


def _restore_wifi_client_fallback(iface: str, use_sudo: bool = True) -> Dict[str, Any]:
    _prepare_wifi_client_iface(iface, use_sudo)
    return {
        'ok': True,
        'message': 'Wi-Fi ready for scan/connect (no saved network)',
    }


def _restore_wifi_client(iface: str, use_sudo: bool = True) -> Dict[str, Any]:
    _prepare_wifi_client_iface(iface, use_sudo)
    raw = _read_file_sudo(WIFI_RESTORE_FILE, use_sudo=use_sudo)
    if not raw.strip():
        return _restore_wifi_client_fallback(iface, use_sudo)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _restore_wifi_client_fallback(iface, use_sudo)

    ssid = str(data.get('ssid', '')).strip()
    if not ssid:
        return _restore_wifi_client_fallback(iface, use_sudo)

    _delete_connections_on_interface(iface, use_sudo)

    profile = str(data.get('profile_name') or f'{iface}-wifi').strip()
    password = str(data.get('password', ''))
    key_mgmt = str(data.get('key_mgmt', 'wpa-psk')).strip() or 'wpa-psk'
    cmd = [
        'nmcli', 'connection', 'add',
        'type', 'wifi',
        'ifname', iface,
        'con-name', profile,
        'ssid', ssid,
        'ipv4.method', 'auto',
        'ipv6.method', 'ignore',
        'connection.autoconnect', 'yes',
    ]
    if key_mgmt != 'none':
        cmd.extend(['wifi-sec.key-mgmt', key_mgmt])
        if password:
            cmd.extend(['wifi-sec.psk', password])
    add = _sudo(cmd, use_sudo=use_sudo, timeout=30)
    if add.returncode != 0:
        return {'ok': False, 'error': add.stderr.strip() or add.stdout.strip()}
    up = _sudo(['nmcli', 'connection', 'up', profile], use_sudo=use_sudo, timeout=45)
    ok = up.returncode == 0
    return {
        'ok': ok,
        'error': '' if ok else (up.stderr.strip() or up.stdout.strip()),
        'message': f'Reconnected to {ssid!r}' if ok else '',
    }


def get_hotspot_iface() -> str:
    return _find_hotspot_iface()


def _write_hotspot_dnsmasq(iface: str, use_sudo: bool = True) -> None:
    board = ipaddress.ip_interface(HOTSPOT_IP_CIDR)
    start = ipaddress.ip_address(HOTSPOT_DHCP_START)
    end = ipaddress.ip_address(HOTSPOT_DHCP_END)
    conf = f"""# Generated by Delatometry webui — personal hotspot
port=0
interface={iface}
bind-dynamic
dhcp-authoritative
dhcp-range={start},{end},{board.network.netmask},{HOTSPOT_LEASE_TIME}
dhcp-option=3,{board.ip}
dhcp-option=6,{board.ip}
"""
    _write_file_sudo(HOTSPOT_DNSMASQ_CONF, conf, use_sudo=use_sudo)
    _write_file_sudo(HOTSPOT_STATE_FILE, iface + '\n', use_sudo=use_sudo)


def _start_hotspot_dnsmasq(use_sudo: bool = True) -> subprocess.CompletedProcess[str]:
    """Enable + start DHCP service (sudoers allows separate enable/start, not enable --now)."""
    en = _sudo(['systemctl', 'enable', HOTSPOT_DHCP_SERVICE], use_sudo=use_sudo, timeout=30)
    if en.returncode != 0:
        return en
    start = _sudo(['systemctl', 'restart', HOTSPOT_DHCP_SERVICE], use_sudo=use_sudo, timeout=30)
    if start.returncode != 0:
        return start
    return _sudo(['systemctl', 'is-active', HOTSPOT_DHCP_SERVICE], use_sudo=use_sudo, timeout=15)


def _stop_hotspot_dnsmasq(use_sudo: bool = True) -> None:
    _sudo(['systemctl', 'stop', HOTSPOT_DHCP_SERVICE], use_sudo=use_sudo, timeout=30)
    _sudo(['systemctl', 'disable', HOTSPOT_DHCP_SERVICE], use_sudo=use_sudo, timeout=30)
    _remove_file_sudo(HOTSPOT_DNSMASQ_CONF, use_sudo=use_sudo)
    _remove_file_sudo(HOTSPOT_STATE_FILE, use_sudo=use_sudo)


def _rollback_hotspot_ap(conn: str, use_sudo: bool = True) -> None:
    _sudo(['nmcli', 'connection', 'down', conn], use_sudo=use_sudo, timeout=20)
    _sudo(['nmcli', 'connection', 'delete', conn], use_sudo=use_sudo, timeout=20)


def enable_personal_hotspot(iface: str = 'wlan0', use_sudo: bool = True) -> Dict[str, Any]:
    if not nmcli_available():
        return {'ok': False, 'error': 'nmcli is not available'}
    iface = iface.strip()
    info = get_interface(iface)
    if not info:
        return {'ok': False, 'error': f'Interface {iface} not found'}
    if info.get('kind') != 'wifi':
        return {'ok': False, 'error': f'{iface} is not a Wi-Fi interface'}

    _stop_hotspot_dnsmasq(use_sudo)
    _snapshot_wifi_client(iface, use_sudo)
    _delete_connections_on_interface(iface, use_sudo)

    conn = _hotspot_connection_name(iface)
    add = _sudo(
        [
            'nmcli', 'connection', 'add',
            'type', 'wifi',
            'ifname', iface,
            'con-name', conn,
            'ssid', HOTSPOT_SSID,
        ],
        use_sudo=use_sudo,
        timeout=30,
    )
    if add.returncode != 0:
        return {'ok': False, 'error': add.stderr.strip() or add.stdout.strip()}

    mod = _sudo(
        [
            'nmcli', 'connection', 'modify', conn,
            '802-11-wireless.mode', 'ap',
            'ipv4.addresses', HOTSPOT_IP_CIDR,
            'ipv4.method', 'manual',
            'ipv4.never-default', 'yes',
            'ipv6.method', 'ignore',
            'connection.autoconnect', 'no',
        ],
        use_sudo=use_sudo,
        timeout=30,
    )
    if mod.returncode != 0:
        return {'ok': False, 'error': mod.stderr.strip() or mod.stdout.strip()}

    up = _sudo(['nmcli', 'connection', 'up', conn], use_sudo=use_sudo, timeout=45)
    if up.returncode != 0:
        return {'ok': False, 'error': up.stderr.strip() or up.stdout.strip()}

    try:
        _write_hotspot_dnsmasq(iface, use_sudo=use_sudo)
    except OSError as exc:
        _rollback_hotspot_ap(conn, use_sudo)
        return {'ok': False, 'error': f'Failed to write hotspot DHCP config: {exc}'}

    dhcp = _start_hotspot_dnsmasq(use_sudo=use_sudo)
    if dhcp.returncode != 0 or dhcp.stdout.strip() != 'active':
        detail = dhcp.stderr.strip() or dhcp.stdout.strip() or 'service not active'
        _rollback_hotspot_ap(conn, use_sudo)
        _stop_hotspot_dnsmasq(use_sudo)
        return {
            'ok': False,
            'error': (
                f'Hotspot DHCP failed: {detail}. '
                'Ensure dnsmasq is installed, delatometry-hotspot-dnsmasq.service is present, '
                'and webui sudoers is installed (scripts/install.sh or install_sudoers.sh).'
            ),
        }
    return {
        'ok': True,
        'error': '',
        'message': (
            f'Hotspot {HOTSPOT_SSID!r} on {iface}: {HOTSPOT_IP_CIDR}, '
            f'clients {HOTSPOT_DHCP_START}–{HOTSPOT_DHCP_END} (open, no password)'
        ),
    }


def disable_personal_hotspot(use_sudo: bool = True) -> Dict[str, Any]:
    iface = _find_hotspot_iface()
    _stop_hotspot_dnsmasq(use_sudo)
    _cleanup_hotspot_connections(use_sudo)

    restore_msg = ''
    restore_ok = True
    if iface:
        info = get_interface(iface)
        if info and info.get('kind') == 'wifi':
            restore = _restore_wifi_client(iface, use_sudo)
            restore_ok = bool(restore.get('ok'))
            restore_msg = str(restore.get('message') or restore.get('error') or '')

    msg = 'Hotspot disabled'
    if restore_msg:
        prefix = '' if restore_ok else 'reconnect failed: '
        msg += f'; {prefix}{restore_msg}' if prefix else f'; {restore_msg}'
    return {'ok': True, 'error': '', 'message': msg, 'iface': iface, 'restore_ok': restore_ok}


def set_interface_admin_state(iface: str, up: bool, use_sudo: bool = True) -> Dict[str, Any]:
    if not nmcli_available():
        return {'ok': False, 'error': 'nmcli is not available'}
    iface = iface.strip()
    if hotspot_is_active(iface) and not up:
        return {'ok': False, 'error': 'Disable hotspot before taking interface down'}
    action = 'connect' if up else 'disconnect'
    proc = _sudo(['nmcli', 'device', action, iface], use_sudo=use_sudo, timeout=30)
    ok = proc.returncode == 0
    return {
        'ok': ok,
        'error': '' if ok else (proc.stderr.strip() or proc.stdout.strip() or f'failed to {action}'),
    }


def configure_interface_dhcp(iface: str, use_sudo: bool = True) -> Dict[str, Any]:
    if not nmcli_available():
        return {'ok': False, 'error': 'nmcli is not available'}
    iface = iface.strip()
    if hotspot_is_active(iface):
        return {'ok': False, 'error': 'Disable hotspot on this interface first'}

    _delete_connections_on_interface(iface, use_sudo)
    conn_type = _connection_type(iface)
    conn_name = f'{iface}-dhcp'
    add = _sudo(
        [
            'nmcli', 'connection', 'add',
            'type', conn_type,
            'ifname', iface,
            'con-name', conn_name,
            'ipv4.method', 'auto',
            'ipv6.method', 'ignore',
            'connection.autoconnect', 'yes',
        ],
        use_sudo=use_sudo,
        timeout=30,
    )
    if add.returncode != 0:
        return {'ok': False, 'error': add.stderr.strip() or add.stdout.strip()}
    up = _sudo(['nmcli', 'connection', 'up', conn_name], use_sudo=use_sudo, timeout=45)
    ok = up.returncode == 0
    return {
        'ok': ok,
        'error': '' if ok else (up.stderr.strip() or up.stdout.strip()),
        'message': f'{iface} set to DHCP',
    }


def configure_interface_static(
    iface: str,
    address: str,
    prefix: int,
    gateway: str = '',
    dns: str = '',
    use_sudo: bool = True,
) -> Dict[str, Any]:
    if not nmcli_available():
        return {'ok': False, 'error': 'nmcli is not available'}
    iface = iface.strip()
    if hotspot_is_active(iface):
        return {'ok': False, 'error': 'Disable hotspot on this interface first'}
    address = address.strip()
    if not address:
        return {'ok': False, 'error': 'IPv4 address is required'}

    cidr = f'{address}/{int(prefix)}'
    _delete_connections_on_interface(iface, use_sudo)
    conn_type = _connection_type(iface)
    conn_name = f'{iface}-static'
    cmd = [
        'nmcli', 'connection', 'add',
        'type', conn_type,
        'ifname', iface,
        'con-name', conn_name,
        'ipv4.addresses', cidr,
        'ipv4.method', 'manual',
        'ipv6.method', 'ignore',
        'connection.autoconnect', 'yes',
    ]
    if gateway.strip():
        cmd.extend(['ipv4.gateway', gateway.strip()])
    if dns.strip():
        cmd.extend(['ipv4.dns', dns.strip()])
    add = _sudo(cmd, use_sudo=use_sudo, timeout=30)
    if add.returncode != 0:
        return {'ok': False, 'error': add.stderr.strip() or add.stdout.strip()}
    up = _sudo(['nmcli', 'connection', 'up', conn_name], use_sudo=use_sudo, timeout=45)
    ok = up.returncode == 0
    return {
        'ok': ok,
        'error': '' if ok else (up.stderr.strip() or up.stdout.strip()),
        'message': f'{iface} static IPv4 {cidr}',
    }


def wifi_scan(iface: str = 'wlan0') -> Dict[str, Any]:
    if not nmcli_available():
        return {'ok': False, 'error': 'nmcli is not available', 'rows': []}
    if hotspot_is_active(iface):
        return {'ok': False, 'error': 'Disable hotspot before Wi-Fi client scan', 'rows': []}
    _sudo(['nmcli', 'device', 'wifi', 'rescan', 'ifname', iface], use_sudo=True, timeout=20)
    result = _run(['nmcli', '-t', '-f', 'IN-USE,SSID,SIGNAL,SECURITY', 'dev', 'wifi', 'list', 'ifname', iface])
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
    interface = interface.strip()
    if hotspot_is_active(interface):
        return {'ok': False, 'error': 'Disable hotspot before connecting as Wi-Fi client'}
    ssid = ssid.strip()
    if not ssid:
        return {'ok': False, 'error': 'SSID is required'}

    _cleanup_hotspot_connections(use_sudo)
    _prepare_wifi_client_iface(interface, use_sudo)

    cmd = ['nmcli', 'dev', 'wifi', 'connect', ssid, 'ifname', interface]
    if password.strip():
        cmd.extend(['password', password.strip()])
    proc = _sudo(cmd, use_sudo=use_sudo, timeout=60)
    ok = proc.returncode == 0
    if ok:
        key_mgmt = 'wpa-psk' if password.strip() else 'none'
        profile_name = ''
        for name in _connections_on_interface(interface):
            fields = _connection_fields(name, use_sudo=use_sudo)
            if fields.get('802-11-wireless.ssid', '').strip() == ssid:
                profile_name = name
                _sudo(
                    ['nmcli', 'connection', 'modify', name, 'connection.autoconnect', 'yes'],
                    use_sudo=use_sudo,
                    timeout=20,
                )
                break
        _save_wifi_restore(
            interface,
            ssid,
            password.strip(),
            key_mgmt,
            profile_name or f'{interface}-wifi',
            use_sudo=use_sudo,
        )
    return {
        'ok': ok,
        'error': '' if ok else (proc.stderr.strip() or proc.stdout.strip()),
        'message': f'Connected to {ssid!r}' if ok else '',
    }


# Backward-compatible wrappers (connection profile name == interface-bound con)
def set_ipv4(connection: str, address: str, prefix: int, gateway: str = '', dns: str = '', use_sudo: bool = True) -> Dict[str, Any]:
    return configure_interface_static(connection, address, prefix, gateway, dns, use_sudo)


def set_dhcp(connection: str, use_sudo: bool = True) -> Dict[str, Any]:
    return configure_interface_dhcp(connection, use_sudo)
