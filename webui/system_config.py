from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from webui.collectors.serial_ports import list_serial_port_options


def read_env_file(path: str) -> Dict[str, str]:
    env_path = Path(path)
    if env_path.is_file():
        return _parse_env_text(env_path.read_text(encoding='utf-8', errors='replace'))

    result = subprocess.run(
        ['sudo', '-n', 'cat', path],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode == 0:
        return _parse_env_text(result.stdout)
    return {}


def _parse_env_text(text: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env_file(path: str, updates: Dict[str, str]) -> Tuple[bool, str]:
    current = read_env_file(path)
    merged = {**current, **{k: str(v) for k, v in updates.items() if k}}

    lines: List[str] = []
    env_path = Path(path)
    if env_path.is_file():
        lines = env_path.read_text(encoding='utf-8', errors='replace').splitlines()
    elif current:
        lines = [f'{k}="{v}"' for k, v in current.items()]
    else:
        lines = ['# Delatometry runtime configuration']

    seen = set()
    new_lines: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith('#') and '=' in stripped:
            key = stripped.split('=', 1)[0].strip()
            if key in merged:
                new_lines.append(f'{key}="{merged[key]}"')
                seen.add(key)
                continue
        new_lines.append(line)

    for key, value in merged.items():
        if key not in seen:
            new_lines.append(f'{key}="{value}"')

    content = '\n'.join(new_lines).rstrip() + '\n'
    use_sudo_tee = str(path).startswith('/etc/') or str(path).startswith('/var/')
    if not use_sudo_tee:
        try:
            env_path.parent.mkdir(parents=True, exist_ok=True)
            env_path.write_text(content, encoding='utf-8')
            return True, 'written (user writable)'
        except PermissionError:
            use_sudo_tee = True

    proc = subprocess.run(
        ['sudo', '-n', 'tee', path],
        input=content,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if proc.returncode == 0:
        return True, 'written via sudo'
    return False, proc.stderr.strip() or proc.stdout.strip() or f'exit {proc.returncode}'


def serial_port_choices(current: str = '') -> List[Dict[str, str]]:
    options = list_serial_port_options()
    devices = {row['device'] for row in options}
    if current and current not in devices:
        options.insert(0, {
            'device': current,
            'label': f'{current} (configured, not detected)',
        })
    if not options:
        for port in ('/dev/ttyUSB0', '/dev/ttyACM0', '/dev/ttyAMA0', '/dev/ttyS0'):
            options.append({'device': port, 'label': port})
    return options


def restart_service(service: str) -> Tuple[bool, str]:
    svc = service if service.endswith('.service') else f'{service}.service'
    proc = subprocess.run(
        ['sudo', '-n', 'systemctl', 'restart', svc],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode == 0:
        return True, f'restarted {svc}'
    return False, proc.stderr.strip() or proc.stdout.strip() or f'failed to restart {svc}'


def get_configuration_snapshot(env_file: str) -> Dict[str, Any]:
    env = read_env_file(env_file)
    return {
        'env_file': env_file,
        'env_readable': bool(env),
        'ltm2985': {
            'port': env.get('DELATOMETRY_LTM2985_PORT', '/dev/ttyUSB0'),
            'baudrate': env.get('DELATOMETRY_LTM2985_BAUDRATE', '230400'),
        },
        'measure_device': {
            'port': env.get('DELATOMETRY_MEASURE_PORT', '/dev/ttyUSB0'),
            'speed': env.get('DELATOMETRY_MEASURE_SPEED', '9600'),
        },
        'measure_source': env.get('DELATOMETRY_MEASURE_SOURCE', 'e720'),
        'measure': {
            'source': env.get('DELATOMETRY_MEASURE_SOURCE', 'e720'),
            'topic_e720': env.get('DELATOMETRY_MEASURE_TOPIC_E720', '/measure_device'),
            'topic_im3536': env.get('DELATOMETRY_MEASURE_TOPIC_IM3536', '/im3536'),
        },
        'im3536': {
            'interface': env.get('DELATOMETRY_IM3536_INTERFACE', 'rs232'),
            'port': env.get('DELATOMETRY_IM3536_PORT', '/dev/ttyUSB0'),
            'baudrate': env.get('DELATOMETRY_IM3536_BAUDRATE', '9600'),
            'host': env.get('DELATOMETRY_IM3536_HOST', '192.168.0.100'),
            'lan_port': env.get('DELATOMETRY_IM3536_LAN_PORT', '23'),
            'terminator': env.get('DELATOMETRY_IM3536_TERMINATOR', 'crlf'),
        },
        'database': {
            'host': env.get('DELATOMETRY_DB_HOST', '127.0.0.1'),
            'port': env.get('DELATOMETRY_DB_PORT', '3306'),
            'name': env.get('DELATOMETRY_DB_NAME', 'exp'),
            'user': env.get('DELATOMETRY_DB_USER', 'delatometry'),
            'password': env.get('DELATOMETRY_DB_PASSWORD', ''),
            'auto_init_schema': env.get('DELATOMETRY_DB_AUTO_INIT_SCHEMA', 'true').lower() == 'true',
        },
        'core': {
            'enable_database_client': env.get('DELATOMETRY_CORE_ENABLE_DATABASE_CLIENT', 'false').lower() == 'true',
            'enable_pwm_controller': env.get('DELATOMETRY_CORE_ENABLE_PWM_CONTROLLER', 'false').lower() == 'true',
            'pwm_pin_ch1': int(env.get('DELATOMETRY_CORE_PWM_PIN_CH1', env.get('DELATOMETRY_CORE_PWM_PIN', '18')) or 18),
            'pwm_pin_ch2': int(env.get('DELATOMETRY_CORE_PWM_PIN_CH2', '19') or 19),
        },
        'ads1256': {
            'enabled': env.get('DELATOMETRY_ADS1256_ENABLED', 'false').lower() == 'true',
            'simulate': env.get('DELATOMETRY_ADS1256_SIMULATE', 'false').lower() == 'true',
            'fallback_to_simulation': env.get('DELATOMETRY_ADS1256_FALLBACK_TO_SIMULATION', 'true').lower() == 'true',
        },
    }
