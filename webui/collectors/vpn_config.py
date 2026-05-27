"""OpenVPN and ZeroTier configuration for Delatometry (persist + boot via systemd)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from shutil import move as _move_file
from pathlib import Path
from typing import Any, Dict, List, Optional

from .network_config import DELATOMETRY_ETC, _sudo

VPN_STATE_FILE = DELATOMETRY_ETC / 'vpn.json'
OPENVPN_DIR = DELATOMETRY_ETC / 'openvpn'
OPENVPN_PROFILE = OPENVPN_DIR / 'client.ovpn'
OPENVPN_AUTH = OPENVPN_DIR / 'auth.txt'
OPENVPN_LOG = Path('/var/log/delatometry-openvpn.log')
OPENVPN_PID = Path('/run/delatometry/openvpn.pid')
VPN_SYSTEMD_UNIT = 'delatometry-vpn.service'

VALID_PROVIDERS = ('none', 'openvpn', 'zerotier')
NETWORK_ID_RE = re.compile(r'^[0-9a-fA-F]{16}$')


def _default_state() -> Dict[str, Any]:
    return {
        'enabled': False,
        'provider': 'none',
        'connect_on_boot': False,
        'zerotier_network_id': '',
        'openvpn_username': '',
        'openvpn_has_profile': False,
    }


def load_state() -> Dict[str, Any]:
    state = _default_state()
    try:
        raw = json.loads(VPN_STATE_FILE.read_text(encoding='utf-8'))
        if isinstance(raw, dict):
            state.update({k: raw[k] for k in state if k in raw})
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError):
        pass
    state['openvpn_has_profile'] = OPENVPN_PROFILE.is_file()
    if state.get('provider') not in VALID_PROVIDERS:
        state['provider'] = 'none'
    return state


def _write_state_file(state: Dict[str, Any], use_sudo: bool = True) -> None:
    payload = {k: state.get(k) for k in _default_state()}
    payload['openvpn_has_profile'] = OPENVPN_PROFILE.is_file()
    text = json.dumps(payload, indent=2) + '\n'
    DELATOMETRY_ETC.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', delete=False) as tmp:
        tmp.write(text)
        tmp_path = tmp.name
    try:
        if use_sudo:
            _sudo(['cp', tmp_path, str(VPN_STATE_FILE)], use_sudo=True)
            _sudo(['chmod', '644', str(VPN_STATE_FILE)], use_sudo=True)
        else:
            _move_file(tmp_path, VPN_STATE_FILE)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _which(name: str) -> Optional[str]:
    return shutil.which(name)


def _read_pid() -> Optional[int]:
    try:
        return int(OPENVPN_PID.read_text(encoding='utf-8').strip())
    except (FileNotFoundError, ValueError):
        return None


def _pid_running(pid: int) -> bool:
    try:
        Path(f'/proc/{pid}').exists()
        return True
    except OSError:
        return False


def _openvpn_running() -> bool:
    pid = _read_pid()
    if pid and _pid_running(pid):
        return True
    proc = subprocess.run(
        ['pgrep', '-f', f'openvpn.*{OPENVPN_PROFILE.name}'],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def _zerotier_network_status(network_id: str) -> Dict[str, Any]:
    network_id = network_id.lower()
    cli = _which('zerotier-cli')
    if not cli:
        return {'joined': False, 'status': 'zerotier-cli not installed'}
    proc = subprocess.run([cli, 'listnetworks'], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return {'joined': False, 'status': proc.stderr.strip() or 'zerotier-cli failed'}
    for line in proc.stdout.splitlines():
        if network_id in line.lower():
            joined = 'OK' in line.upper() or 'ACCESS_DENIED' not in line.upper()
            return {'joined': True, 'status': line.strip()}
    return {'joined': False, 'status': 'not joined'}


def get_status() -> Dict[str, Any]:
    state = load_state()
    provider = str(state.get('provider') or 'none')
    connected = False
    detail = 'VPN disabled'

    if provider == 'openvpn' and state.get('enabled'):
        if _openvpn_running():
            connected = True
            detail = 'OpenVPN running'
        elif state.get('openvpn_has_profile'):
            detail = 'OpenVPN configured, not connected'
        else:
            detail = 'Upload an .ovpn profile'
    elif provider == 'zerotier' and state.get('enabled'):
        nid = str(state.get('zerotier_network_id') or '').strip()
        if nid:
            zt = _zerotier_network_status(nid)
            connected = bool(zt.get('joined'))
            detail = zt.get('status', '')
        else:
            detail = 'Enter ZeroTier network ID'

    unit_enabled = False
    proc = subprocess.run(
        ['systemctl', 'is-enabled', VPN_SYSTEMD_UNIT],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0 and 'enabled' in proc.stdout:
        unit_enabled = True

    return {
        'state': state,
        'connected': connected,
        'detail': detail,
        'boot_service_enabled': unit_enabled,
        'openvpn_installed': bool(_which('openvpn')),
        'zerotier_installed': bool(_which('zerotier-cli')),
    }


def _ensure_openvpn_auth_in_profile(use_sudo: bool) -> None:
    if not OPENVPN_AUTH.is_file() or not OPENVPN_PROFILE.is_file():
        return
    try:
        text = OPENVPN_PROFILE.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return
    auth_line = f'auth-user-pass {OPENVPN_AUTH}'
    if 'auth-user-pass' in text:
        return
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', delete=False) as tmp:
        tmp.write(text.rstrip() + '\n' + auth_line + '\n')
        tmp_path = tmp.name
    try:
        _sudo(['cp', tmp_path, str(OPENVPN_PROFILE)], use_sudo=use_sudo)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def save_openvpn_profile(content: bytes, use_sudo: bool = True) -> Dict[str, Any]:
    if not content or len(content) < 32:
        return {'ok': False, 'error': 'Empty or invalid .ovpn file'}
    if b'client' not in content.lower() and b'remote' not in content.lower():
        return {'ok': False, 'error': 'File does not look like an OpenVPN client profile'}
    OPENVPN_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('wb', delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        _sudo(['cp', tmp_path, str(OPENVPN_PROFILE)], use_sudo=use_sudo)
        _sudo(['chmod', '600', str(OPENVPN_PROFILE)], use_sudo=use_sudo)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    state = load_state()
    state['openvpn_has_profile'] = True
    _write_state_file(state, use_sudo=use_sudo)
    return {'ok': True, 'error': '', 'message': 'OpenVPN profile saved'}


def save_openvpn_credentials(username: str, password: str, use_sudo: bool = True) -> None:
    username = username.strip()
    password = password.strip()
    if not username:
        try:
            if use_sudo:
                _sudo(['rm', '-f', str(OPENVPN_AUTH)], use_sudo=True)
            else:
                OPENVPN_AUTH.unlink(missing_ok=True)
        except OSError:
            pass
        return
    OPENVPN_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', delete=False) as tmp:
        tmp.write(f'{username}\n{password}\n')
        tmp_path = tmp.name
    try:
        _sudo(['cp', tmp_path, str(OPENVPN_AUTH)], use_sudo=use_sudo)
        _sudo(['chmod', '600', str(OPENVPN_AUTH)], use_sudo=use_sudo)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    _ensure_openvpn_auth_in_profile(use_sudo=use_sudo)


def _set_boot_service(enabled: bool, use_sudo: bool = True) -> Dict[str, Any]:
    if enabled:
        en = _sudo(['systemctl', 'enable', VPN_SYSTEMD_UNIT], use_sudo=use_sudo, timeout=30)
        if en.returncode != 0:
            return {'ok': False, 'error': en.stderr.strip() or 'systemctl enable failed'}
        st = _sudo(['systemctl', 'start', VPN_SYSTEMD_UNIT], use_sudo=use_sudo, timeout=60)
        if st.returncode != 0:
            return {'ok': False, 'error': st.stderr.strip() or 'systemctl start failed'}
    else:
        _sudo(['systemctl', 'stop', VPN_SYSTEMD_UNIT], use_sudo=use_sudo, timeout=30)
        _sudo(['systemctl', 'disable', VPN_SYSTEMD_UNIT], use_sudo=use_sudo, timeout=30)
    return {'ok': True, 'error': ''}


def disconnect_vpn(use_sudo: bool = True) -> Dict[str, Any]:
    errors: List[str] = []
    pid = _read_pid()
    if pid:
        _sudo(['kill', str(pid)], use_sudo=use_sudo, timeout=10)
        OPENVPN_PID.unlink(missing_ok=True)
    _sudo(['pkill', '-f', f'openvpn.*{OPENVPN_PROFILE}'], use_sudo=use_sudo, timeout=10)

    state = load_state()
    nid = str(state.get('zerotier_network_id') or '').strip()
    if nid and _which('zerotier-cli'):
        proc = _sudo(['zerotier-cli', 'leave', nid], use_sudo=use_sudo, timeout=30)
        if proc.returncode != 0:
            errors.append(proc.stderr.strip() or 'zerotier leave failed')

    if errors:
        return {'ok': False, 'error': '; '.join(errors)}
    return {'ok': True, 'error': '', 'message': 'VPN disconnected'}


def connect_openvpn(use_sudo: bool = True) -> Dict[str, Any]:
    if not _which('openvpn'):
        return {'ok': False, 'error': 'openvpn is not installed (apt install openvpn)'}
    if not OPENVPN_PROFILE.is_file():
        return {'ok': False, 'error': f'Missing profile: {OPENVPN_PROFILE}'}
    disconnect_vpn(use_sudo=use_sudo)
    OPENVPN_PID.parent.mkdir(parents=True, exist_ok=True)
    OPENVPN_LOG.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        'openvpn',
        '--daemon',
        '--config', str(OPENVPN_PROFILE),
        '--writepid', str(OPENVPN_PID),
        '--log', str(OPENVPN_LOG),
        '--verb', '3',
    ]
    proc = _sudo(cmd, use_sudo=use_sudo, timeout=30)
    if proc.returncode != 0:
        return {'ok': False, 'error': proc.stderr.strip() or proc.stdout.strip() or 'openvpn failed'}
    time.sleep(1.5)
    if not _openvpn_running():
        tail = ''
        try:
            tail = OPENVPN_LOG.read_text(encoding='utf-8', errors='replace')[-500:]
        except OSError:
            pass
        return {'ok': False, 'error': f'OpenVPN exited. Log: {tail or "see " + str(OPENVPN_LOG)}'}
    return {'ok': True, 'error': '', 'message': 'OpenVPN connected'}


def connect_zerotier(network_id: str, use_sudo: bool = True) -> Dict[str, Any]:
    network_id = network_id.strip().lower()
    if not NETWORK_ID_RE.match(network_id):
        return {'ok': False, 'error': 'ZeroTier network ID must be 16 hex characters'}
    if not _which('zerotier-cli'):
        return {'ok': False, 'error': 'zerotier-cli not installed (install zerotier-one package)'}
    en = _sudo(['systemctl', 'enable', '--now', 'zerotier-one'], use_sudo=use_sudo, timeout=45)
    if en.returncode != 0:
        return {'ok': False, 'error': en.stderr.strip() or 'failed to start zerotier-one'}
    proc = _sudo(['zerotier-cli', 'join', network_id], use_sudo=use_sudo, timeout=60)
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip()
        if 'already' not in err.lower():
            return {'ok': False, 'error': err or 'zerotier join failed'}
    zt = _zerotier_network_status(network_id)
    return {
        'ok': True,
        'error': '',
        'message': f'ZeroTier join requested: {zt.get("status", network_id)}',
    }


def connect_vpn(use_sudo: bool = True) -> Dict[str, Any]:
    state = load_state()
    if not state.get('enabled') or state.get('provider') == 'none':
        return {'ok': False, 'error': 'VPN is not enabled in settings'}
    provider = state.get('provider')
    if provider == 'openvpn':
        return connect_openvpn(use_sudo=use_sudo)
    if provider == 'zerotier':
        return connect_zerotier(str(state.get('zerotier_network_id', '')), use_sudo=use_sudo)
    return {'ok': False, 'error': f'Unknown provider: {provider}'}


def save_settings(
    *,
    provider: str,
    enabled: bool,
    connect_on_boot: bool,
    zerotier_network_id: str = '',
    openvpn_username: str = '',
    openvpn_password: str = '',
    connect_now: bool = False,
    use_sudo: bool = True,
) -> Dict[str, Any]:
    provider = (provider or 'none').strip().lower()
    if provider not in VALID_PROVIDERS:
        return {'ok': False, 'error': f'Invalid provider: {provider}'}

    if provider == 'zerotier' and enabled:
        nid = zerotier_network_id.strip().lower()
        if not NETWORK_ID_RE.match(nid):
            return {'ok': False, 'error': 'ZeroTier network ID must be 16 hex characters'}

    if provider == 'openvpn' and enabled and not OPENVPN_PROFILE.is_file():
        return {'ok': False, 'error': 'Upload an OpenVPN .ovpn profile first'}

    state = load_state()
    state['provider'] = provider
    state['enabled'] = bool(enabled) and provider != 'none'
    state['connect_on_boot'] = bool(connect_on_boot) and state['enabled']
    state['zerotier_network_id'] = zerotier_network_id.strip().lower()
    state['openvpn_username'] = openvpn_username.strip()

    if provider == 'openvpn':
        if openvpn_password.strip():
            save_openvpn_credentials(openvpn_username, openvpn_password, use_sudo=use_sudo)
        elif not openvpn_username.strip():
            save_openvpn_credentials('', '', use_sudo=use_sudo)

    _write_state_file(state, use_sudo=use_sudo)

    boot = state['enabled'] and state['connect_on_boot']
    boot_result = _set_boot_service(boot, use_sudo=use_sudo)
    if not boot_result.get('ok'):
        return boot_result

    if not state['enabled']:
        disconnect_vpn(use_sudo=use_sudo)
        return {'ok': True, 'error': '', 'message': 'VPN disabled'}

    if connect_now:
        return connect_vpn(use_sudo=use_sudo)

    return {'ok': True, 'error': '', 'message': 'VPN settings saved'}


def vpn_boot_connect() -> None:
    """Called by delatometry-vpn.service on boot (runs as root)."""
    state = load_state()
    if not state.get('enabled') or not state.get('connect_on_boot'):
        return
    result = connect_vpn(use_sudo=False)
    if not result.get('ok'):
        raise RuntimeError(result.get('error', 'VPN connect failed'))
