from __future__ import annotations

import json
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

import rclpy
from database.srv import Query as DatabaseQuery
from msgs.msg import Ads, E720, Measurement
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import String, UInt8

from webui.collectors import db_test, gpio_pins, host_stats, network_config, network_info, serial_ports, systemd_ops, vpn_config
from webui.param_utils import ros_param_bool
from webui.program_steps import parse_step_field_updates
from webui.temperature_validation import suggest_next_step, validate_new_program, validate_temperature_steps
from webui.e720_sweep import E720SweepConfig, E720SweepController, STANDARD_FREQUENCIES, SWEEP_MODE_LABELS
from webui.e720_view import e720_from_msg, e720_summary_text, e720_table_row
from webui.experiment_runner import ExperimentRunner, ExperimentState, ProgramStep
from webui.export_data import export_program_archive
from webui.measurement_log import build_measurement_row, insert_measurement
from webui.system_config import (
    get_configuration_snapshot,
    read_env_file,
    restart_service,
    serial_port_choices,
    write_env_file,
)
from webui.ros_message import message_to_dict
from webui.dataframe_utils import parse_temperature_steps
from webui.collectors import network_config

CoreQuery = DatabaseQuery

DEFAULT_SYSTEMD_UNITS = [
    'delatometry-database.service',
    'delatometry-ltm2985.service',
    'delatometry-measure-device.service',
    'delatometry-ads1256.service',
    'delatometry-core.service',
    'delatometry-hmi.service',
    'delatometry-webui.service',
]


class WebHMINode(Node):
    def __init__(self) -> None:
        super().__init__('webui')

        self.declare_parameter('core_service', '/core/query')
        self.declare_parameter('database_service', '/database/query')
        self.declare_parameter('measurement_topic', '/ltm2985/measurement')
        self.declare_parameter('measure_topic', '/measure_device')
        self.declare_parameter('measure_command_topic', '/measure_device/command')
        self.declare_parameter('bind_host', '0.0.0.0')
        self.declare_parameter('bind_port', 80)
        self.declare_parameter('title', 'Delatometry Control')
        self.declare_parameter('queue_enabled', False)
        self.declare_parameter('auth_enabled', False)
        self.declare_parameter('auth_user', 'admin')
        self.declare_parameter('auth_password', 'admin')
        self.declare_parameter('status_refresh_period_sec', 1.0)
        self.declare_parameter('control_loop_period_sec', 1.0)
        self.declare_parameter('delatometry_env_file', '/etc/default/delatometry')
        self.declare_parameter('export_dir', '')
        self.declare_parameter('enable_service_control', True)
        self.declare_parameter('network_use_sudo', True)
        self.declare_parameter('enable_measurement_logging', True)
        self.declare_parameter('measurement_log_control_channel', 9)
        self.declare_parameter('measurement_log_monitor_channel', 3)
        self.declare_parameter('ltm_raw_topic', '/ltm2985/raw_json')
        self.declare_parameter('ads_topic', '/ads1256')
        self.declare_parameter('stream_max_lines', 30)
        self.declare_parameter('ltm_control_channel', 9)
        self.declare_parameter('ltm_monitor_channel', 3)
        self.declare_parameter('systemd_units', DEFAULT_SYSTEMD_UNITS)

        self.core_service = str(self.get_parameter('core_service').value)
        self.database_service = str(self.get_parameter('database_service').value)
        self.measurement_topic = str(self.get_parameter('measurement_topic').value)
        self.measure_topic = str(self.get_parameter('measure_topic').value)
        self.measure_command_topic = str(self.get_parameter('measure_command_topic').value)
        self.bind_host = str(self.get_parameter('bind_host').value)
        self.bind_port = int(self.get_parameter('bind_port').value)
        self.title = str(self.get_parameter('title').value)
        self.queue_enabled = bool(self.get_parameter('queue_enabled').value)
        self.auth_enabled = ros_param_bool(self.get_parameter('auth_enabled').value)
        self.auth_user = str(self.get_parameter('auth_user').value)
        self.auth_password = str(self.get_parameter('auth_password').value)
        self.status_refresh_period_sec = float(self.get_parameter('status_refresh_period_sec').value)
        self.control_loop_period_sec = float(self.get_parameter('control_loop_period_sec').value)
        self.delatometry_env_file = str(self.get_parameter('delatometry_env_file').value)
        self.enable_service_control = bool(self.get_parameter('enable_service_control').value)
        self.network_use_sudo = bool(self.get_parameter('network_use_sudo').value)
        self.enable_measurement_logging = bool(self.get_parameter('enable_measurement_logging').value)
        self.log_control_channel = int(self.get_parameter('measurement_log_control_channel').value)
        self.log_monitor_channel = int(self.get_parameter('measurement_log_monitor_channel').value)
        self.ltm_raw_topic = str(self.get_parameter('ltm_raw_topic').value)
        self.ads_topic = str(self.get_parameter('ads_topic').value)
        self.stream_max_lines = int(self.get_parameter('stream_max_lines').value)
        self.ltm_control_channel = int(self.get_parameter('ltm_control_channel').value)
        self.ltm_monitor_channel = int(self.get_parameter('ltm_monitor_channel').value)

        export_dir = str(self.get_parameter('export_dir').value).strip()
        self.export_dir = export_dir or str(Path(tempfile.gettempdir()) / 'delatometry_exports')

        units_param = self.get_parameter('systemd_units').value
        self.systemd_units = (
            [str(u) for u in units_param]
            if isinstance(units_param, (list, tuple)) and units_param
            else list(DEFAULT_SYSTEMD_UNITS)
        )

        self.core_client = self.create_client(CoreQuery, self.core_service)
        self.db_client = self.create_client(DatabaseQuery, self.database_service)
        self._e720_cmd_pub = self.create_publisher(UInt8, self.measure_command_topic, 10)
        stream_len = max(10, self.stream_max_lines)
        self.create_subscription(Measurement, self.measurement_topic, self._on_measurement, 100)
        self.create_subscription(E720, self.measure_topic, self._on_e720, 10)
        self.create_subscription(String, self.ltm_raw_topic, self._on_ltm_raw, 20)
        self.create_subscription(Ads, self.ads_topic, self._on_ads, 10)

        self._runner = ExperimentRunner()
        self._sweep = E720SweepController()
        self._experiment = ExperimentState()

        self._lock = threading.RLock()
        self._programs_nav_program_id = 0
        self._new_program_draft: List[List[Any]] = []
        self._new_program_description: str = ''
        self._new_program_sweep_mode: int = 0
        self._new_program_enabled_freqs: List[str] = ['1000']
        self._new_program_range_max: float = 10000.0
        self._last_topic_peek: Dict[str, str] = {}
        self._last_wifi_scan: List[List[Any]] = []
        self._last_db_test_msg = ''
        self._latest_measurements: Dict[int, Dict[str, Any]] = {}
        self._latest_e720: Optional[E720] = None
        self._latest_ads: Optional[Ads] = None
        self._latest_ltm_raw: Optional[str] = None
        self._ltm_stream: Deque[str] = deque(maxlen=stream_len)
        self._e720_stream: Deque[str] = deque(maxlen=stream_len)
        self._last_core_snapshot: Dict[str, Any] = {}
        self._log_lines: Deque[str] = deque(maxlen=300)
        self._last_service_log_time: Dict[str, float] = {}
        self._last_measurement_log_monotonic: float = 0.0

        self._control_timer = self.create_timer(self.control_loop_period_sec, self._control_tick)
        self._log('webui node started')

    def _log(self, message: str) -> None:
        stamp = time.strftime('%Y-%m-%d %H:%M:%S')
        with self._lock:
            self._log_lines.appendleft(f'[{stamp}] {message}')
        self.get_logger().info(message)

    def _log_throttled(self, key: str, message: str, period_sec: float = 5.0) -> None:
        now = time.monotonic()
        with self._lock:
            if now - self._last_service_log_time.get(key, 0.0) < period_sec:
                return
            self._last_service_log_time[key] = now
        self._log(message)

    def _stream_stamp(self) -> str:
        return time.strftime('%H:%M:%S')

    def _on_measurement(self, msg: Measurement) -> None:
        ch = int(msg.channel)
        mtype = str(msg.type)
        value = float(msg.value)
        valid = bool(msg.valid)
        line = f'[{self._stream_stamp()}] ch={ch} {mtype}={value:.4g} valid={valid}'
        with self._lock:
            self._latest_measurements[ch] = {
                'channel': ch,
                'type': mtype,
                'value': value,
                'valid': valid,
                'updated_monotonic': time.monotonic(),
            }
            if 'temperature' in mtype.lower():
                self._ltm_stream.appendleft(line)

    def _on_e720(self, msg: E720) -> None:
        data = e720_from_msg(msg)
        line = (
            f'[{self._stream_stamp()}] E7-20 '
            f"{'ON' if data.get('online') else 'OFF'} "
            f"f={data.get('frequency', 0):.3g} "
            f"ch1={data.get('firstvalue', 0):.4g} ch2={data.get('secondvalue', 0):.4g}"
        )
        with self._lock:
            self._latest_e720 = msg
            self._e720_stream.appendleft(line)

    def _on_ltm_raw(self, msg: String) -> None:
        with self._lock:
            self._latest_ltm_raw = str(msg.data)

    def _on_ads(self, msg: Ads) -> None:
        with self._lock:
            self._latest_ads = msg

    def _call_service_json(
        self,
        client: Any,
        request_type: Any,
        service_name: str,
        payload: Dict[str, Any],
        timeout_sec: float = 5.0,
    ) -> Dict[str, Any]:
        if not client.wait_for_service(timeout_sec=timeout_sec):
            raise RuntimeError(f'Service is not available: {service_name}')
        request = request_type()
        request.query = json.dumps(payload)
        future = client.call_async(request)
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if future.done():
                response = future.result()
                if response is None:
                    raise RuntimeError(f'Service call failed: {service_name}')
                return json.loads(response.response or '{}')
            time.sleep(0.02)
        raise TimeoutError(f'Timeout waiting for {service_name}')

    def _db_query(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._call_service_json(self.db_client, DatabaseQuery.Request, self.database_service, payload)

    def _core_query(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = self._call_service_json(self.core_client, CoreQuery.Request, self.core_service, payload)
        with self._lock:
            self._last_core_snapshot = result
        return result

    def _service_available(self, client: Any, timeout_sec: float = 0.05) -> bool:
        try:
            return client.service_is_ready() or bool(client.wait_for_service(timeout_sec=timeout_sec))
        except Exception:
            return False

    def _db_available(self) -> bool:
        return self._service_available(self.db_client)

    def _core_available(self) -> bool:
        return self._service_available(self.core_client)

    def _critical_services_status(self) -> Dict[str, Any]:
        core_ok = self._core_available()
        db_ok = self._db_available()
        missing = [s for s, ok in ((self.core_service, core_ok), (self.database_service, db_ok)) if not ok]
        tc = self._last_core_snapshot.get('temperature_control') or {}
        return {
            'core_available': core_ok,
            'database_available': db_ok,
            'system_ready': core_ok and db_ok,
            'missing_required_services': missing,
            'temperature_control_enabled': tc.get('enabled'),
            'pwm_note': (
                'Heater PWM disabled in core — set DELATOMETRY_CORE_ENABLE_PWM_CONTROLLER=true'
                if not tc and core_ok
                else None
            ),
        }

    def _database_unavailable_message(self) -> str:
        msg = f'CRITICAL: database service unavailable: {self.database_service}'
        self._log_throttled('database_unavailable', msg)
        return msg

    def _core_unavailable_message(self) -> str:
        msg = f'CRITICAL: core service unavailable: {self.core_service}'
        self._log_throttled('core_unavailable', msg)
        return msg

    def _get_program_steps(self, program_id: int) -> List[ProgramStep]:
        if not self._db_available():
            raise RuntimeError(self._database_unavailable_message())
        response = self._db_query({'cmd': 'program_step_list', 'id': program_id})
        if response.get('result') != 'Ok':
            raise RuntimeError(response.get('error', 'Failed to load program steps'))
        return ExperimentRunner.parse_steps(response.get('row', []))

    def _load_e720_config_for_program(self, program_id: int) -> None:
        if program_id <= 0 or not self._db_available():
            return
        try:
            response = self._db_query({'cmd': 'get_e720', 'id': program_id})
            if response.get('result') == 'Ok' and response.get('row'):
                self._sweep.load_config(E720SweepConfig.from_db_row(response['row']))
        except Exception as exc:
            self._log(f'Could not load E7-20 config for program {program_id}: {exc}')

    def _publish_e720_byte(self, byte_val: int) -> None:
        msg = UInt8()
        msg.data = int(byte_val) & 0xFF
        self._e720_cmd_pub.publish(msg)

    def _maybe_log_measurement(self, target_k: Optional[float]) -> None:
        if not self.enable_measurement_logging or not self._db_available():
            return
        with self._lock:
            program_id = self._experiment.program_id
            e720_msg = self._latest_e720
            temps = dict(self._latest_measurements)
        if program_id is None:
            return
        e720 = e720_from_msg(e720_msg)
        if not e720.get('online'):
            return
        now = time.monotonic()
        if now - self._last_measurement_log_monotonic < self.control_loop_period_sec * 0.9:
            return
        row = build_measurement_row(
            program_id,
            e720,
            temps,
            self.log_control_channel,
            self.log_monitor_channel,
            target_k,
        )
        try:
            if insert_measurement(self._db_query, row):
                self._last_measurement_log_monotonic = now
        except Exception as exc:
            self._log_throttled('measurement_log', f'Measurement log failed: {exc}')

    def _control_tick(self) -> None:
        try:
            with self._lock:
                running = self._experiment.program_id is not None
                e720_data = e720_from_msg(self._latest_e720)

            if running:
                sweep_result = self._sweep.tick(float(e720_data.get('frequency', 0) or 0), running=True)
                cmd = sweep_result.get('command_byte')
                if cmd is not None:
                    self._publish_e720_byte(int(cmd))

            action = self._runner.tick(self._experiment)
            if not action.get('active'):
                if action.get('finished'):
                    self._finish_active_program('Finished')
                return

            target_k = action.get('target_k')
            if target_k is not None:
                self._core_query({'temperature_control': {'enabled': True, 'target_k': float(target_k)}})
                self._experiment.last_target_k = float(target_k)
                if action.get('step_started') or action.get('advanced_step'):
                    pid = self._experiment.program_id
                    step = self._experiment.step_index + 1
                    total = len(self._experiment.steps)
                    self._log(f'Program {pid}: step {step}/{total}, target {target_k:.2f} K')
                self._maybe_log_measurement(float(target_k))
        except Exception as exc:
            self._log(f'Control loop error: {exc}')

    def _finish_active_program(self, final_status: str) -> None:
        program_id = self._experiment.program_id
        if program_id is None:
            return
        try:
            self._core_query({'temperature_control': {'enabled': False}})
        except Exception as exc:
            self._log(f'Failed to disable control: {exc}')
        try:
            self._db_query({'cmd': 'program_update_status', 'id': program_id, 'status': final_status})
        except Exception as exc:
            self._log(f'Failed to update program status: {exc}')
        self._experiment = ExperimentState()
        self._sweep.reset()
        self._log(f'Program {program_id} ended: {final_status}')

    def _experiment_banner(self) -> str:
        exp = self._experiment
        if exp.program_id is None:
            return f'Idle — last status: {exp.status}'
        step = exp.step_index + 1
        total = len(exp.steps)
        target = exp.last_target_k
        mode = SWEEP_MODE_LABELS.get(self._sweep.config.mode, 'unknown')
        return (
            f'RUNNING program {exp.program_id} — step {step}/{total}, '
            f'target {target:.2f} K, E7-20 mode: {mode}'
            if target is not None
            else f'RUNNING program {exp.program_id} — step {step}/{total}, E7-20 mode: {mode}'
        )

    def _is_ltm_temperature(self, item: Dict[str, Any]) -> bool:
        mtype = str(item.get('type', '')).lower()
        return 'temperature' in mtype

    def _measurements_table(self) -> List[List[Any]]:
        now = time.monotonic()
        with self._lock:
            items = sorted(self._latest_measurements.items())
        return [
            [ch, item['type'], item['value'], item['valid'], round(max(0.0, now - item['updated_monotonic']), 3)]
            for ch, item in items
            if self._is_ltm_temperature(item)
        ]

    def _ltm_temperature_summary(self) -> str:
        now = time.monotonic()
        with self._lock:
            items = dict(self._latest_measurements)
        control = items.get(self.ltm_control_channel)
        monitor = items.get(self.ltm_monitor_channel)
        lines = [
            f'Topic: {self.measurement_topic}',
            f'Control ch {self.ltm_control_channel}: '
            + (
                f'{control["value"]:.4f} {control["type"]} (age {now - control["updated_monotonic"]:.1f}s)'
                if control and self._is_ltm_temperature(control)
                else '—'
            ),
            f'Monitor ch {self.ltm_monitor_channel}: '
            + (
                f'{monitor["value"]:.4f} {monitor["type"]} (age {now - monitor["updated_monotonic"]:.1f}s)'
                if monitor and self._is_ltm_temperature(monitor)
                else '—'
            ),
            f'All LTM temp channels: {sum(1 for v in items.values() if self._is_ltm_temperature(v))}',
        ]
        exp = self._experiment
        if exp.program_id is not None:
            lines.append(f'Program {exp.program_id} running — step {exp.step_index + 1}/{len(exp.steps)}')
        return '\n'.join(lines)

    def _stream_text(self, stream: Deque[str]) -> str:
        with self._lock:
            lines = list(stream)
        return '\n'.join(lines) if lines else '(no messages yet)'

    def _programs_table(self) -> List[List[Any]]:
        if not self._db_available():
            return []
        response = self._db_query({'cmd': 'program_all_list'})
        if response.get('result') != 'Ok':
            return []
        rows = []
        for raw_row in response.get('row', []):
            parts = str(raw_row).split('^')
            if len(parts) >= 3:
                rows.append([int(parts[0]), parts[1], parts[2]])
        return rows

    def _steps_table(self, program_id: int) -> List[List[Any]]:
        return [[s.step_id, s.t_start, s.t_stop, s.minutes] for s in self._get_program_steps(program_id)]

    # --- UI handlers ---
    def ui_tick_general(self) -> Tuple[Any, ...]:
        host = host_stats.collect_host_stats()
        host_summary = (
            {
                'cpu_percent': host.get('cpu_percent'),
                'load_avg': host.get('load_avg'),
                'memory_percent': host.get('memory_percent'),
                'memory_used_gb': host.get('memory_used_gb'),
                'memory_total_gb': host.get('memory_total_gb'),
            }
            if host.get('available')
            else {'error': host.get('error')}
        )
        return (
            self._critical_services_status(),
            host_summary,
            systemd_ops.units_table(self.systemd_units),
            host.get('disk_rows', []),
            serial_ports.uart_table(self.delatometry_env_file),
            network_info.interfaces_table(),
            '\n'.join(list(self._log_lines)),
        )

    def ui_tick_experiment(self) -> Tuple[Any, ...]:
        with self._lock:
            e720_msg = self._latest_e720
            ltm_stream = self._stream_text(self._ltm_stream)
            e720_stream = self._stream_text(self._e720_stream)
        e720_data = e720_from_msg(e720_msg)
        core_json = json.dumps(self._last_core_snapshot.get('temperature_control') or {}, indent=2)
        return (
            self._experiment_banner(),
            self._ltm_temperature_summary(),
            self._measurements_table(),
            ltm_stream,
            e720_summary_text(e720_data),
            [e720_table_row(e720_data)],
            e720_stream,
            core_json,
        )

    def ui_peek_ltm_topic(self) -> str:
        with self._lock:
            measurements = {
                str(ch): dict(item)
                for ch, item in sorted(self._latest_measurements.items())
            }
            raw = self._latest_ltm_raw
        payload = {
            'measurement_topic': self.measurement_topic,
            'raw_topic': self.ltm_raw_topic,
            'measurements': measurements,
            'latest_raw_json': raw,
        }
        text = json.dumps(payload, indent=2)
        with self._lock:
            self._last_topic_peek['ltm'] = text
        return text

    def ui_peek_e720_topic(self) -> str:
        with self._lock:
            msg = self._latest_e720
        text = json.dumps(
            {'topic': self.measure_topic, 'message': message_to_dict(msg)},
            indent=2,
            default=str,
        )
        with self._lock:
            self._last_topic_peek['e720'] = text
        return text

    def ui_peek_ads_topic(self) -> str:
        with self._lock:
            msg = self._latest_ads
        if msg is None:
            text = json.dumps(
                {'topic': self.ads_topic, 'message': None, 'hint': 'No message yet — is the node enabled?'},
                indent=2,
            )
        else:
            text = json.dumps({'topic': self.ads_topic, 'message': message_to_dict(msg)}, indent=2, default=str)
        with self._lock:
            self._last_topic_peek['ads'] = text
        return text

    def ui_peek_hmi_topic(self) -> str:
        with self._lock:
            e720_msg = self._latest_e720
            ads_msg = self._latest_ads
        text = json.dumps(
            {
                'note': 'HMI consumes these topics (UART is fixed on-board)',
                'measure_topic': self.measure_topic,
                'e720': message_to_dict(e720_msg),
                'ads_topic': self.ads_topic,
                'ads': message_to_dict(ads_msg),
            },
            indent=2,
            default=str,
        )
        with self._lock:
            self._last_topic_peek['hmi'] = text
        return text

    def ui_test_database_connection(
        self,
        host: str,
        port: float,
        name: str,
        user: str,
        password: str,
    ) -> str:
        ok, msg = db_test.test_database_connection(host, int(port), name, user, password)
        self._log(f'DB test: {msg}')
        result = f'OK: {msg}' if ok else f'FAILED: {msg}'
        with self._lock:
            self._last_db_test_msg = result
        return result

    def ui_tick_network(self) -> List[List[Any]]:
        return network_info.interfaces_table()

    def _iface_summary(self, iface: str) -> str:
        row = network_info.get_interface(iface)
        if not row:
            return f'{iface}: not found'
        return (
            f"{iface} ({row['kind']}) — {'up' if row['up'] else 'down'}, "
            f"MAC {row['mac'] or '—'}, IPv4 {row['ipv4']}"
        )

    def _hotspot_status_text(self, iface: str = '') -> str:
        if network_config.hotspot_is_active():
            active = ''
            try:
                active = network_config.HOTSPOT_STATE_FILE.read_text(encoding='utf-8').strip()
            except OSError:
                pass
            return f'Hotspot ACTIVE on {active or iface} — SSID {network_config.HOTSPOT_SSID}'
        return 'Hotspot off'

    def ui_refresh_network(self) -> Tuple[Any, ...]:
        return (
            network_info.interfaces_table(),
            self._iface_summary('eth0'),
            self._hotspot_status_text(),
        )

    def ui_select_network_interface(self, iface: str) -> Tuple[str, str, float, bool, str]:
        iface = str(iface or 'eth0').strip()
        parsed = network_info.parse_primary_ipv4(iface)
        info = network_info.get_interface(iface)
        is_wifi = bool(info and info.get('kind') == 'wifi')
        return (
            self._iface_summary(iface),
            parsed['address'],
            float(parsed['prefix']),
            is_wifi,
            self._hotspot_status_text(iface),
        )

    def ui_net_up(self, iface: str) -> Tuple[str, List[List[Any]], str]:
        result = network_config.set_interface_admin_state(str(iface), True, use_sudo=self.network_use_sudo)
        msg = result.get('message') or ('OK' if result.get('ok') else result.get('error', 'failed'))
        self._log(f'net up {iface}: {msg}')
        return msg, network_info.interfaces_table(), self._iface_summary(str(iface))

    def ui_net_down(self, iface: str) -> Tuple[str, List[List[Any]], str]:
        result = network_config.set_interface_admin_state(str(iface), False, use_sudo=self.network_use_sudo)
        msg = result.get('message') or ('OK' if result.get('ok') else result.get('error', 'failed'))
        self._log(f'net down {iface}: {msg}')
        return msg, network_info.interfaces_table(), self._iface_summary(str(iface))

    def ui_net_dhcp(self, iface: str) -> Tuple[str, List[List[Any]], str]:
        result = network_config.configure_interface_dhcp(str(iface), use_sudo=self.network_use_sudo)
        msg = result.get('message') or ('OK' if result.get('ok') else result.get('error', 'failed'))
        self._log(f'net dhcp {iface}: {msg}')
        return msg, network_info.interfaces_table(), self._iface_summary(str(iface))

    def ui_net_apply_static(
        self,
        iface: str,
        address: str,
        prefix: float,
        gateway: str,
        dns: str,
    ) -> Tuple[str, List[List[Any]], str]:
        result = network_config.configure_interface_static(
            str(iface),
            str(address),
            int(prefix),
            str(gateway),
            str(dns),
            use_sudo=self.network_use_sudo,
        )
        msg = result.get('message') or ('OK' if result.get('ok') else result.get('error', 'failed'))
        self._log(f'net static {iface}: {msg}')
        return msg, network_info.interfaces_table(), self._iface_summary(str(iface))

    def ui_hotspot_enable(self, iface: str) -> Tuple[str, str, List[List[Any]]]:
        result = network_config.enable_personal_hotspot(str(iface or 'wlan0'), use_sudo=self.network_use_sudo)
        msg = result.get('message') or ('OK' if result.get('ok') else result.get('error', 'failed'))
        self._log(f'hotspot enable: {msg}')
        return msg, self._hotspot_status_text(str(iface)), network_info.interfaces_table()

    def ui_hotspot_disable(self) -> Tuple[str, str, List[List[Any]]]:
        result = network_config.disable_personal_hotspot(use_sudo=self.network_use_sudo)
        msg = result.get('message') or ('OK' if result.get('ok') else result.get('error', 'failed'))
        self._log(f'hotspot disable: {msg}')
        return msg, self._hotspot_status_text(), network_info.interfaces_table()

    def ui_vpn_save(
        self,
        provider: str,
        enabled: bool,
        connect_on_boot: bool,
        zerotier_network_id: str,
        openvpn_username: str,
        openvpn_password: str,
        connect_now: bool,
    ) -> str:
        result = vpn_config.save_settings(
            provider=provider,
            enabled=enabled,
            connect_on_boot=connect_on_boot,
            zerotier_network_id=zerotier_network_id,
            openvpn_username=openvpn_username,
            openvpn_password=openvpn_password,
            connect_now=connect_now,
            use_sudo=self.network_use_sudo,
        )
        msg = result.get('message') or ('OK' if result.get('ok') else result.get('error', 'failed'))
        self._log(f'vpn save: {msg}')
        return msg

    def ui_vpn_upload_profile(self, content: bytes) -> str:
        result = vpn_config.save_openvpn_profile(content, use_sudo=self.network_use_sudo)
        msg = result.get('message') or ('OK' if result.get('ok') else result.get('error', 'failed'))
        self._log(f'vpn upload: {msg}')
        return msg

    def ui_vpn_connect(self) -> str:
        result = vpn_config.connect_vpn(use_sudo=self.network_use_sudo)
        msg = result.get('message') or ('OK' if result.get('ok') else result.get('error', 'failed'))
        self._log(f'vpn connect: {msg}')
        return msg

    def ui_vpn_disconnect(self) -> str:
        result = vpn_config.disconnect_vpn(use_sudo=self.network_use_sudo)
        msg = result.get('message') or ('OK' if result.get('ok') else result.get('error', 'failed'))
        self._log(f'vpn disconnect: {msg}')
        return msg

    def _save_env_section(
        self,
        updates: Dict[str, str],
        service: str,
        restart: bool,
    ) -> str:
        ok, msg = write_env_file(self.delatometry_env_file, updates)
        if not ok:
            return f'Failed to write {self.delatometry_env_file}: {msg}'
        saved = read_env_file(self.delatometry_env_file)
        for key, val in updates.items():
            if saved.get(key) != str(val):
                return (
                    f'Write reported success but {key} mismatch in {self.delatometry_env_file} '
                    f'(got {saved.get(key)!r}). Check sudoers for tee.'
                )
        self._log(f'Configuration updated: {", ".join(updates.keys())} -> {self.delatometry_env_file}')
        if not restart:
            return f'Saved ({msg}). Restart {service} to apply.'
        ok_r, msg_r = restart_service(service)
        if service.endswith('webui.service'):
            return f'Saved ({msg}). Skipped webui restart — refresh page or restart from SSH.'
        if ok_r:
            return f'Saved and {msg_r}.'
        return f'Saved ({msg}) but restart failed: {msg_r}'

    def ui_save_ltm2985_config(self, port: str, baudrate: float, restart: bool) -> Tuple[str, str]:
        msg = self._save_env_section(
            {
                'DELATOMETRY_LTM2985_PORT': str(port).strip(),
                'DELATOMETRY_LTM2985_BAUDRATE': str(int(baudrate)),
            },
            'delatometry-ltm2985.service',
            bool(restart),
        )
        return msg, msg

    def ui_save_measure_device_config(self, port: str, speed: float, restart: bool) -> Tuple[str, str]:
        msg = self._save_env_section(
            {
                'DELATOMETRY_MEASURE_PORT': str(port).strip(),
                'DELATOMETRY_MEASURE_SPEED': str(int(speed)),
            },
            'delatometry-measure-device.service',
            bool(restart),
        )
        return msg, msg

    def ui_save_database_config(
        self,
        host: str,
        port: float,
        name: str,
        user: str,
        password: str,
        auto_init: bool,
        restart: bool,
    ) -> Tuple[str, str]:
        msg = self._save_env_section(
            {
                'DELATOMETRY_DB_HOST': str(host).strip(),
                'DELATOMETRY_DB_PORT': str(int(port)),
                'DELATOMETRY_DB_NAME': str(name).strip(),
                'DELATOMETRY_DB_USER': str(user).strip(),
                'DELATOMETRY_DB_PASSWORD': str(password),
                'DELATOMETRY_DB_AUTO_INIT_SCHEMA': 'true' if auto_init else 'false',
            },
            'delatometry-database.service',
            bool(restart),
        )
        return msg, msg

    def ui_save_core_config(
        self,
        pwm_ch1: str,
        pwm_ch2: str,
        enable_db_client: bool,
        enable_pwm: bool,
        restart: bool,
    ) -> Tuple[str, str]:
        msg = self._save_env_section(
            {
                'DELATOMETRY_CORE_PWM_PIN_CH1': str(int(float(pwm_ch1))),
                'DELATOMETRY_CORE_PWM_PIN_CH2': str(int(float(pwm_ch2))),
                'DELATOMETRY_CORE_ENABLE_DATABASE_CLIENT': 'true' if enable_db_client else 'false',
                'DELATOMETRY_CORE_ENABLE_PWM_CONTROLLER': 'true' if enable_pwm else 'false',
            },
            'delatometry-core.service',
            bool(restart),
        )
        return msg, msg

    def ui_save_ads1256_config(
        self,
        enabled: bool,
        simulate: bool,
        fallback: bool,
        restart: bool,
    ) -> Tuple[str, str]:
        ok_env, msg_env = write_env_file(
            self.delatometry_env_file,
            {
                'DELATOMETRY_ADS1256_ENABLED': 'true' if enabled else 'false',
                'DELATOMETRY_ADS1256_SIMULATE': 'true' if simulate else 'false',
                'DELATOMETRY_ADS1256_FALLBACK_TO_SIMULATION': 'true' if fallback else 'false',
            },
        )
        if not ok_env:
            return f'Failed to write env: {msg_env}', f'Failed to write env: {msg_env}'
        ok_svc, msg_svc = gpio_pins.set_service_enabled('delatometry-ads1256.service', bool(enabled))
        parts = [f'env: {msg_env}', f'service: {msg_svc}']
        if bool(restart) and enabled:
            ok_r, msg_r = restart_service('delatometry-ads1256.service')
            parts.append(msg_r if ok_r else f'restart failed: {msg_r}')
        elif bool(restart) and not enabled:
            parts.append('service stopped (disabled)')
        msg = '; '.join(parts)
        self._log(f'ADS1256 config: {msg}')
        return msg, msg

    def ui_service_control(self, unit: str, action: str) -> str:
        if not self.enable_service_control:
            return 'Service control disabled (enable_service_control:=true).'
        unit_name = str(unit).strip()
        if not unit_name.endswith('.service'):
            unit_name = f'{unit_name}.service'
        action_l = action.strip().lower()
        blocked = systemd_ops.unit_action_blocked(unit_name, action_l)
        if blocked:
            return blocked
        result = systemd_ops.control_unit(unit_name, action_l, use_sudo=True)
        message = f'{action} {unit}: {"OK" if result["ok"] else "FAILED"}'
        if result.get('error'):
            message += f' — {result["error"]}'
        self._log(message)
        return message

    def ui_refresh_programs(self) -> Tuple[List[List[Any]], str]:
        if not self._db_available():
            return [], self._database_unavailable_message()
        rows = self._programs_table()
        return rows, f'{len(rows)} program(s).'

    def ui_load_program(self, program_id: float) -> Tuple[List[List[Any]], str, str]:
        if not self._db_available():
            return [], self._database_unavailable_message(), ''
        program_id_int = int(program_id)
        try:
            rows = self._steps_table(program_id_int)
            self._load_e720_config_for_program(program_id_int)
            stats = self._db_query({'cmd': 'measurement_stats', 'program_id': program_id_int})
            stats_txt = json.dumps(stats.get('row', stats), indent=2)
            return rows, f'{len(rows)} step(s) for program {program_id_int}.', stats_txt
        except Exception as exc:
            return [], f'ERROR: {exc}', ''

    def ui_create_program(self) -> Tuple[float, List[List[Any]], str]:
        if not self._db_available():
            return 0.0, [], self._database_unavailable_message()
        try:
            response = self._db_query({'cmd': 'new_program'})
            if response.get('result') != 'Ok':
                return 0.0, self._programs_table(), f'ERROR: {response.get("error", "unknown")}'
            program_id = int(response.get('ID', 0))
            self._log(f'Created program {program_id}')
            return float(program_id), self._programs_table(), f'Created program {program_id}.'
        except Exception as exc:
            return 0.0, [], f'ERROR: {exc}'

    def ui_duplicate_program(self, program_id: float) -> Tuple[float, List[List[Any]], str]:
        if not self._db_available():
            return 0.0, [], self._database_unavailable_message()
        source_id = int(program_id)
        if source_id <= 0:
            return 0.0, self._programs_table(), 'Select a program to duplicate.'
        try:
            created = self._db_query({'cmd': 'new_program'})
            if created.get('result') != 'Ok':
                return 0.0, self._programs_table(), 'Failed to create new program.'
            new_id = int(created.get('ID', 0))
            for step in self._get_program_steps(source_id):
                self._db_query({
                    'cmd': 'program_step_insert',
                    'program_id': new_id,
                    't_start': step.t_start,
                    't_stop': step.t_stop,
                    'minutes': step.minutes,
                })
            e720 = self._db_query({'cmd': 'get_e720', 'id': source_id})
            if e720.get('result') == 'Ok' and e720.get('row'):
                row = dict(e720['row'])
                row['id'] = new_id
                self._db_query({'cmd': 'set_e720', **row})
            self._log(f'Duplicated program {source_id} -> {new_id}')
            return float(new_id), self._programs_table(), f'Duplicated as program {new_id}.'
        except Exception as exc:
            return 0.0, [], f'ERROR: {exc}'

    def ui_delete_program(self, program_id: float) -> Tuple[List[List[Any]], str, float]:
        if not self._db_available():
            return [], self._database_unavailable_message(), program_id
        program_id_int = int(program_id)
        if program_id_int <= 0:
            return self._programs_table(), 'Invalid program ID.', program_id
        if self._experiment.program_id == program_id_int:
            return self._programs_table(), 'Stop the running program first.', program_id
        try:
            response = self._db_query({'cmd': 'program_delete_by_id', 'id': program_id_int})
            if response.get('result') != 'Ok':
                return self._programs_table(), f'ERROR: {response.get("error", "delete failed")}', program_id
            self._log(f'Deleted program {program_id_int}')
            return self._programs_table(), f'Deleted program {program_id_int}.', 0.0
        except Exception as exc:
            return [], f'ERROR: {exc}', program_id

    def ui_clear_measurements(self, program_id: float) -> str:
        if not self._db_available():
            return self._database_unavailable_message()
        program_id_int = int(program_id)
        if program_id_int <= 0:
            return 'Select a valid program ID.'
        try:
            response = self._db_query({'cmd': 'measurement_delete_by_program_id', 'program_id': program_id_int})
            count = response.get('count', 0)
            self._log(f'Cleared {count} measurement(s) for program {program_id_int}')
            return f'Deleted {count} measurement row(s) for program {program_id_int}.'
        except Exception as exc:
            return f'ERROR: {exc}'

    def ui_add_step(self, program_id: float, t_start: float, t_stop: float, minutes: float) -> Tuple[List[List[Any]], str]:
        if not self._db_available():
            return [], self._database_unavailable_message()
        program_id_int = int(program_id)
        try:
            response = self._db_query({
                'cmd': 'program_step_insert',
                'program_id': program_id_int,
                't_start': float(t_start),
                't_stop': float(t_stop),
                'minutes': float(minutes),
            })
            if response.get('result') != 'Ok':
                return self._steps_table(program_id_int), f'ERROR: {response.get("error", "unknown")}'
            return self._steps_table(program_id_int), 'Step added.'
        except Exception as exc:
            return [], f'ERROR: {exc}'

    def ui_delete_step(self, program_id: float, step_id: float) -> Tuple[List[List[Any]], str]:
        if not self._db_available():
            return [], self._database_unavailable_message()
        program_id_int = int(program_id)
        try:
            response = self._db_query({'cmd': 'program_delete_temp', 'id': int(step_id)})
            if response.get('result') != 'Ok':
                return self._steps_table(program_id_int), f'ERROR: {response.get("error", "unknown")}'
            return self._steps_table(program_id_int), f'Deleted step {int(step_id)}.'
        except Exception as exc:
            return [], f'ERROR: {exc}'

    def ui_save_e720_config(
        self,
        program_id: float,
        mode: float,
        enabled_freqs: List[str],
        range_max: float,
    ) -> str:
        if not self._db_available():
            return self._database_unavailable_message()
        program_id_int = int(program_id)
        if program_id_int <= 0:
            return 'Select a valid program ID.'
        freqs = {int(float(x)) for x in (enabled_freqs or [])}
        if not freqs:
            freqs = {1000}
        config = E720SweepConfig(
            mode=int(mode),
            enabled_frequencies=freqs,
            range_min_hz=min(freqs),
            range_max_hz=float(range_max),
        )
        self._sweep.load_config(config)
        try:
            payload = config.to_db_payload(program_id_int)
            response = self._db_query({'cmd': 'set_e720', **payload})
            if response.get('result') != 'Ok':
                return f'ERROR: {response.get("error", "save failed")}'
            self._log(f'Saved E7-20 sweep config for program {program_id_int}')
            return f'Saved E7-20 config: {SWEEP_MODE_LABELS.get(config.mode, mode)}.'
        except Exception as exc:
            return f'ERROR: {exc}'

    def ui_start_program(self, program_id: float) -> Tuple[str, str]:
        if not self._db_available():
            return self._database_unavailable_message(), self._experiment_banner()
        if not self._core_available():
            return self._core_unavailable_message(), self._experiment_banner()
        program_id_int = int(program_id)
        try:
            steps = self._get_program_steps(program_id_int)
            if not steps:
                return f'Program {program_id_int} has no steps.', self._experiment_banner()
            if self._experiment.program_id is not None:
                return 'Another program is already running.', self._experiment_banner()
            self._load_e720_config_for_program(program_id_int)
            first_target_k = float(steps[0].t_start)
            self._core_query({
                'temperature_control': {'enabled': True, 'target_k': first_target_k, 'reset_integral': True},
            })
            self._db_query({'cmd': 'program_update_status', 'id': program_id_int, 'status': 'Running'})
            self._experiment = ExperimentState(
                program_id=program_id_int,
                steps=steps,
                step_index=0,
                step_started_monotonic=None,
                status='Running',
                last_target_k=first_target_k,
            )
            self._sweep.reset()
            self._last_measurement_log_monotonic = 0.0
            self._log(f'Started program {program_id_int} ({len(steps)} steps)')
            return f'Program {program_id_int} started.', self._experiment_banner()
        except Exception as exc:
            return f'ERROR: {exc}', self._experiment_banner()

    def ui_stop_program(self) -> Tuple[str, str]:
        if not self._core_available() and self._experiment.program_id is not None:
            return self._core_unavailable_message(), self._experiment_banner()
        program_id = self._experiment.program_id
        if program_id is None:
            try:
                self._core_query({'temperature_control': {'enabled': False}})
                return 'No active program. Control disabled.', self._experiment_banner()
            except Exception as exc:
                return f'ERROR: {exc}', self._experiment_banner()
        self._finish_active_program('Stopped')
        return f'Program {program_id} stopped.', self._experiment_banner()

    def ui_manual_target(self, target_k: float, enabled: bool) -> str:
        if not self._core_available():
            return json.dumps({'error': self._core_unavailable_message()}, indent=2)
        try:
            response = self._core_query({
                'temperature_control': {'enabled': bool(enabled), 'target_k': float(target_k)},
            })
            return json.dumps(response.get('temperature_control') or {}, indent=2)
        except Exception as exc:
            return json.dumps({'error': str(exc)}, indent=2)

    def ui_export_program(self, program_id: float, limit: float, clear_first: bool) -> Tuple[Optional[str], str]:
        if not self._db_available():
            return None, self._database_unavailable_message()
        program_id_int = int(program_id)
        if program_id_int <= 0:
            return None, 'Select a valid program ID.'
        try:
            if clear_first:
                self._db_query({'cmd': 'measurement_delete_by_program_id', 'program_id': program_id_int})
            result = export_program_archive(self._db_query, program_id_int, self.export_dir, limit=int(limit))
            if not result.get('ok'):
                return None, result.get('error', 'export failed')
            self._log(f'Exported program {program_id_int} -> {result["zip_path"]}')
            return result['zip_path'], (
                f'Export OK: {result["measurement_count"]} measurements, {result["step_count"]} steps.'
            )
        except Exception as exc:
            return None, f'ERROR: {exc}'

    def ui_e720_send_byte(self, byte_val: int) -> str:
        byte_val = int(byte_val) & 0xFF
        self._publish_e720_byte(byte_val)
        self._log(f'E7-20 command: byte {byte_val}')
        return f'Sent byte {byte_val} on {self.measure_command_topic}'

    def ui_wifi_scan(self, iface: str) -> Tuple[List[List[Any]], str]:
        result = network_config.wifi_scan(str(iface or 'wlan0'))
        if not result.get('ok'):
            return [], result.get('error', 'scan failed')
        rows = result.get('rows', [])
        with self._lock:
            self._last_wifi_scan = rows
        return rows, f'Found {len(rows)} network(s).'

    def ui_wifi_connect(self, ssid: str, password: str, iface: str) -> str:
        result = network_config.wifi_connect(
            ssid,
            password,
            str(iface or 'wlan0'),
            use_sudo=self.network_use_sudo,
        )
        msg = 'Connected' if result.get('ok') else result.get('error', 'failed')
        self._log(f'Wi-Fi {ssid} on {iface}: {msg}')
        return msg

    # --- Web UI context helpers ---
    def get_dashboard_snapshot(self) -> Dict[str, Any]:
        host = host_stats.collect_host_stats()
        host_summary = (
            {
                'cpu_percent': host.get('cpu_percent'),
                'load_avg': host.get('load_avg'),
                'memory_percent': host.get('memory_percent'),
                'memory_used_gb': host.get('memory_used_gb'),
                'memory_total_gb': host.get('memory_total_gb'),
            }
            if host.get('available')
            else {'error': host.get('error')}
        )
        return {
            'services': self._critical_services_status(),
            'host': host_summary,
            'units': systemd_ops.units_table(self.systemd_units),
            'disks': host.get('disk_rows', []),
            'uart': serial_ports.uart_table(self.delatometry_env_file),
            'interfaces': network_info.interfaces_table(),
            'log_text': '\n'.join(list(self._log_lines)),
        }

    def get_dashboard_context(self) -> Dict[str, Any]:
        snap = self.get_dashboard_snapshot()
        return {
            'title': self.title,
            'refresh_sec': self.status_refresh_period_sec,
            'enable_service_control': self.enable_service_control,
            **snap,
        }

    def get_configuration_context(self, iface: Optional[str] = None) -> Dict[str, Any]:
        snap = get_configuration_snapshot(self.delatometry_env_file)
        ifaces = network_info.list_manageable_interfaces()
        selected = str(iface or '').strip()
        if not selected:
            selected = 'eth0' if 'eth0' in ifaces else (ifaces[0] if ifaces else 'eth0')
        if selected not in ifaces and ifaces:
            selected = ifaces[0]
        net = self._network_iface_context(selected)
        with self._lock:
            peek = dict(self._last_topic_peek)
            peek['db_test'] = self._last_db_test_msg
            wifi_rows = list(self._last_wifi_scan)
        core = snap['core']
        return {
            'title': self.title,
            'env_file': self.delatometry_env_file,
            'status': (
                f'Loaded {self.delatometry_env_file}'
                if snap.get('env_readable')
                else f'Warning: could not read {self.delatometry_env_file} (using defaults)'
            ),
            'interfaces': network_info.interfaces_table(),
            'iface_choices': ifaces,
            'ltm': snap['ltm2985'],
            'meas': snap['measure_device'],
            'db': snap['database'],
            'core': core,
            'ads': snap['ads1256'],
            'ltm_port_choices': serial_port_choices(snap['ltm2985']['port']),
            'meas_port_choices': serial_port_choices(snap['measure_device']['port']),
            'pwm_pins': gpio_pins.bcm_pin_choices(core['pwm_pin_ch1']),
            'hotspot_ssid': network_config.HOTSPOT_SSID,
            'wifi_networks': wifi_rows,
            'vpn': vpn_config.get_status(),
            'peek': peek,
            **net,
        }

    def _network_iface_context(self, iface: str) -> Dict[str, Any]:
        summary, address, prefix, is_wifi, hotspot = self.ui_select_network_interface(iface)
        return {
            'selected_iface': iface,
            'iface_info': summary,
            'net_ip': address,
            'net_prefix': prefix,
            'is_wifi': is_wifi,
            'hotspot_status': hotspot,
        }

    def get_experiment_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            e720_msg = self._latest_e720
            ltm_lines = list(self._ltm_stream)
            e720_lines = list(self._e720_stream)
            items = dict(self._latest_measurements)
            core = dict(self._last_core_snapshot.get('temperature_control') or {})
        e720_data = e720_from_msg(e720_msg)
        control = items.get(self.ltm_control_channel)
        control_temp = None
        if control and self._is_ltm_temperature(control):
            control_temp = float(control['value'])
        return {
            'banner': self._experiment_banner(),
            'ltm_summary': self._ltm_temperature_summary(),
            'measurements': self._measurements_table(),
            'ltm_stream': ltm_lines,
            'e720_summary': e720_summary_text(e720_data),
            'e720_row': e720_table_row(e720_data),
            'e720_stream': e720_lines,
            'core_json': core,
            'control_temp': control_temp,
        }

    def clear_new_program_draft(self) -> None:
        with self._lock:
            self._new_program_draft = []
            self._new_program_description = ''
            self._new_program_sweep_mode = 0
            self._new_program_enabled_freqs = ['1000']
            self._new_program_range_max = 10000.0

    def get_new_program_draft_meta(self) -> Dict[str, Any]:
        with self._lock:
            return {
                'description': self._new_program_description,
                'sweep_mode': self._new_program_sweep_mode,
                'enabled_freqs': list(self._new_program_enabled_freqs),
                'range_max': self._new_program_range_max,
            }

    def set_new_program_draft_meta(
        self,
        description: Optional[str] = None,
        sweep_mode: Optional[int] = None,
        enabled_freqs: Optional[List[str]] = None,
        range_max: Optional[float] = None,
    ) -> None:
        with self._lock:
            if description is not None:
                self._new_program_description = str(description).strip()
            if sweep_mode is not None:
                self._new_program_sweep_mode = int(sweep_mode)
            if enabled_freqs is not None:
                self._new_program_enabled_freqs = [str(f) for f in enabled_freqs if str(f).strip()]
            if range_max is not None:
                self._new_program_range_max = float(range_max)

    def sync_new_program_draft_from_form(self, form: Any) -> None:
        """Keep description, steps, and E7-20 draft in sync with the wizard form."""
        enabled = form.getlist('enabled_freqs') if hasattr(form, 'getlist') else []
        if not enabled and form.get('enabled_freqs'):
            raw = form.get('enabled_freqs')
            enabled = raw if isinstance(raw, list) else [raw]
        try:
            sweep_mode = int(form.get('sweep_mode', 0) or 0)
        except (TypeError, ValueError):
            sweep_mode = 0
        try:
            range_max = float(form.get('range_max', 10000) or 10000)
        except (TypeError, ValueError):
            range_max = 10000.0
        self.set_new_program_draft_meta(
            description=str(form.get('description', '') or ''),
            sweep_mode=sweep_mode,
            enabled_freqs=list(enabled),
            range_max=range_max,
        )
        self.update_new_program_draft_from_form(parse_step_field_updates(form))

    def get_new_program_draft(self) -> List[List[Any]]:
        with self._lock:
            return [list(row) for row in self._new_program_draft]

    def suggest_new_program_step_defaults(self) -> Tuple[float, float, float]:
        with self._lock:
            draft = [list(row) for row in self._new_program_draft]
        return suggest_next_step(draft)

    def add_new_program_draft_step(self, t_start: float, t_stop: float, minutes: float) -> str:
        with self._lock:
            draft = [list(row) for row in self._new_program_draft]
        next_id = len(draft) + 1
        candidate = draft + [[next_id, float(t_start), float(t_stop), float(minutes)]]
        ok, issues = validate_temperature_steps(candidate)
        if not ok:
            return issues[0].message if issues else 'Invalid temperature step.'
        with self._lock:
            self._new_program_draft.append([next_id, float(t_start), float(t_stop), float(minutes)])
        return ''

    def remove_new_program_draft_step(self, step_id: int) -> None:
        with self._lock:
            idx = int(step_id) - 1
            if 0 <= idx < len(self._new_program_draft):
                self._new_program_draft.pop(idx)
            for i, row in enumerate(self._new_program_draft):
                row[0] = i + 1

    def ui_program_create_from_draft(
        self,
        description: str,
        mode: int,
        enabled_freqs: List[str],
        range_max: float,
    ) -> str:
        with self._lock:
            draft = [list(row) for row in self._new_program_draft]
        check = validate_new_program(description, draft, int(mode), enabled_freqs, float(range_max))
        if not check.can_create:
            return check.issues[0].message if check.issues else 'Cannot create program.'
        return self.ui_program_create_save_new_page(description, draft, float(mode), enabled_freqs, range_max)

    # --- Programs ---
    def ui_programs_set_nav(self, program_id: int) -> None:
        with self._lock:
            self._programs_nav_program_id = int(program_id or 0)

    @staticmethod
    def _parse_program_id(program_id: Any) -> int:
        try:
            return int(float(str(program_id or '').strip() or 0))
        except (TypeError, ValueError):
            return 0

    def ui_programs_list_refresh(self) -> Tuple[List[List[Any]], str]:
        if not self._db_available():
            return [], self._database_unavailable_message()
        rows = self._programs_table()
        return rows, f'{len(rows)} program(s) in database.'

    def ui_programs_nav_view(self, program_id: int) -> Tuple[int, str]:
        pid = int(program_id or 0)
        self.ui_programs_set_nav(pid)
        return pid, 'view'

    def ui_programs_nav_edit(self, program_id: int) -> Tuple[int, str]:
        pid = int(program_id or 0)
        self.ui_programs_set_nav(pid)
        return pid, 'edit'

    def ui_programs_delete_row(self, program_id: int) -> Tuple[List[List[Any]], str]:
        return self.ui_programs_action_delete(program_id)

    def ui_programs_export_row(self, program_id: int) -> Tuple[Optional[str], str]:
        return self.ui_programs_action_export(program_id)

    def ui_programs_action_delete(self, program_id: Any) -> Tuple[List[List[Any]], str]:
        pid = self._parse_program_id(program_id)
        if not self._db_available():
            return [], self._database_unavailable_message()
        if pid <= 0:
            return self._programs_table(), 'Select a program first.'
        if self._experiment.program_id == pid:
            return self._programs_table(), 'Stop the running program before deleting it.'
        try:
            self._db_query({'cmd': 'measurement_delete_by_program_id', 'program_id': pid})
            response = self._db_query({'cmd': 'program_delete_by_id', 'id': pid})
            if response.get('result') != 'Ok':
                msg = f'Delete failed: {response.get("error", "unknown")}'
            else:
                msg = f'Program {pid} and its data were deleted.'
                self._log(msg)
            rows = self._programs_table()
            return rows, msg
        except Exception as exc:
            return [], f'ERROR: {exc}'

    def ui_programs_action_export(self, program_id: Any) -> Tuple[Optional[str], str]:
        pid = self._parse_program_id(program_id)
        if not self._db_available():
            return None, self._database_unavailable_message()
        if pid <= 0:
            return None, 'Select a program to export.'
        try:
            result = export_program_archive(self._db_query, pid, self.export_dir, limit=50000)
            if not result.get('ok'):
                return None, result.get('error', 'export failed')
            self._log(f'Exported program {pid} -> {result["zip_path"]}')
            return result['zip_path'], (
                f'Export ready: {result["measurement_count"]} measurements, '
                f'{result["step_count"]} steps.'
            )
        except Exception as exc:
            return None, f'ERROR: {exc}'

    def program_edit_fields(self, program_id: int) -> Dict[str, Any]:
        pid = int(program_id or 0)
        if not self._db_available() or pid <= 0:
            return {
                'header': 'Select a program on the list first.',
                'description': '',
                'status': '',
                'steps': [],
                'sweep_mode': 0,
                'enabled_freqs': ['1000'],
                'range_max': 10000.0,
                'run_status': self._experiment_banner(),
            }
        try:
            detail = self._db_query({'cmd': 'get_program_detail', 'id': pid})
            if detail.get('result') != 'Ok':
                return {
                    'header': f'Program {pid}',
                    'description': '',
                    'status': '',
                    'steps': [],
                    'sweep_mode': 0,
                    'enabled_freqs': ['1000'],
                    'range_max': 10000.0,
                    'run_status': detail.get('error', 'not found'),
                }
            row = detail['row']
            e720 = row.get('e720') or {}
            cfg_raw = e720.get('config') if isinstance(e720.get('config'), dict) else {}
            enabled = [str(int(x)) for x in cfg_raw.get('enabled_frequencies', [1000])]
            steps = [
                [s['step_id'], s['t_start'], s['t_stop'], s['minutes']]
                for s in row.get('steps', [])
            ]
            self._load_e720_config_for_program(pid)
            return {
                'header': f"Program {row['id']} — {row['datetime']} — status: {row['status']}",
                'description': str(row.get('description') or ''),
                'status': str(row.get('status') or ''),
                'steps': steps,
                'sweep_mode': int(e720.get('param', 0) or 0),
                'enabled_freqs': enabled,
                'range_max': float(cfg_raw.get('range_max_hz', 10000) or 10000),
                'run_status': self._experiment_banner(),
            }
        except Exception as exc:
            return {
                'header': f'Program {pid}',
                'description': '',
                'status': '',
                'steps': [],
                'sweep_mode': 0,
                'enabled_freqs': ['1000'],
                'range_max': 10000.0,
                'run_status': f'ERROR: {exc}',
            }

    def program_view_fields(self, program_id: int) -> Dict[str, Any]:
        pid = int(program_id or 0)
        if not self._db_available() or pid <= 0:
            return {
                'summary': 'Select a program on the list first.',
                'steps': [],
                'e720_json': '{}',
                'stats_json': '{}',
                'message': 'Select a program first.',
            }
        try:
            detail = self._db_query({'cmd': 'get_program_detail', 'id': pid})
            if detail.get('result') != 'Ok':
                return {
                    'summary': f'Program {pid}',
                    'steps': [],
                    'e720_json': '{}',
                    'stats_json': '{}',
                    'message': detail.get('error', 'not found'),
                }
            row = detail['row']
            steps = [[s['step_id'], s['t_start'], s['t_stop'], s['minutes']] for s in row.get('steps', [])]
            desc = (row.get('description') or '').strip() or '_No description_'
            summary = (
                f"## Program {row['id']}\n\n"
                f"- **Created:** {row['datetime']}\n"
                f"- **Status:** {row['status']}\n"
                f"- **Description:** {desc}\n"
                f"- **Temperature steps:** {len(steps)}\n"
            )
            stats = row.get('measurement_stats') or {}
            return {
                'summary': summary,
                'steps': steps,
                'e720_json': json.dumps(row.get('e720') or {}, indent=2, default=str),
                'stats_json': json.dumps(stats, indent=2, default=str),
                'message': f'Loaded program {pid}.',
            }
        except Exception as exc:
            return {
                'summary': f'Program {pid}',
                'steps': [],
                'e720_json': '{}',
                'stats_json': '{}',
                'message': f'ERROR: {exc}',
            }

    def ui_program_create_save_new_page(
        self,
        description: str,
        draft_steps: Any,
        mode: float,
        enabled_freqs: List[str],
        range_max: float,
    ) -> str:
        if not self._db_available():
            return self._database_unavailable_message()
        steps = []
        for row in draft_steps or []:
            if len(row) >= 4:
                steps.append({
                    't_start': float(row[1]),
                    't_stop': float(row[2]),
                    'minutes': float(row[3]),
                })
        if not steps:
            return 'Add at least one temperature step before creating the program.'
        try:
            created = self._db_query({'cmd': 'new_program'})
            if created.get('result') != 'Ok':
                return f'ERROR: {created.get("error", "could not create program")}'
            program_id = int(created.get('ID', 0))
            self._db_query({
                'cmd': 'set_program_meta',
                'program_id': program_id,
                'key': 'description',
                'value': str(description or '').strip(),
            })
            freqs = {int(float(x)) for x in (enabled_freqs or [])} or {1000}
            config = E720SweepConfig(
                mode=int(mode),
                enabled_frequencies=freqs,
                range_min_hz=float(min(freqs)),
                range_max_hz=float(range_max),
            )
            self._db_query({'cmd': 'set_e720', **config.to_db_payload(program_id)})
            for step in steps:
                self._db_query({
                    'cmd': 'program_step_insert',
                    'program_id': program_id,
                    **step,
                })
            self.ui_programs_set_nav(program_id)
            self.clear_new_program_draft()
            self._log(f'Created program {program_id}')
            return f'Program {program_id} created.'
        except Exception as exc:
            return f'ERROR: {exc}'

    def _active_program_id(self) -> int:
        with self._lock:
            return int(self._programs_nav_program_id)

    @staticmethod
    def _step_fields_unchanged(current: ProgramStep, fields: Dict[str, float]) -> bool:
        return (
            abs(float(current.t_start) - float(fields['t_start'])) < 1e-6
            and abs(float(current.t_stop) - float(fields['t_stop'])) < 1e-6
            and abs(float(current.minutes) - float(fields['minutes'])) < 1e-6
        )

    def ui_program_update_single_step(
        self,
        program_id: int,
        step_id: int,
        t_start: float,
        t_stop: float,
        minutes: float,
    ) -> Tuple[bool, str]:
        if not self._db_available():
            return False, self._database_unavailable_message()
        program_id = int(program_id)
        step_id = int(step_id)
        if program_id <= 0 or step_id <= 0:
            return False, 'Invalid program or step id.'
        try:
            current = {s.step_id: s for s in self._get_program_steps(program_id)}
            fields = {'t_start': float(t_start), 't_stop': float(t_stop), 'minutes': float(minutes)}
            cur = current.get(step_id)
            if cur and self._step_fields_unchanged(cur, fields):
                return True, f'Step {step_id} unchanged.'
            response = self._db_query({
                'cmd': 'program_step_update',
                'id': step_id,
                'program_id': program_id,
                't_start': fields['t_start'],
                't_stop': fields['t_stop'],
                'minutes': fields['minutes'],
            })
            if response.get('result') != 'Ok':
                return False, f'Step {step_id}: {response.get("error", "update failed")}'
            return True, f'Step {step_id} saved.'
        except Exception as exc:
            return False, str(exc)

    def ui_program_edit_save_steps(self, program_id: int, step_updates: Dict[int, Dict[str, float]]) -> Optional[str]:
        if not step_updates:
            return None
        try:
            current = {s.step_id: s for s in self._get_program_steps(program_id)}
        except Exception as exc:
            return str(exc)
        for step_id, fields in sorted(step_updates.items()):
            if len(fields) < 3:
                return f'Step {step_id}: missing t_start, t_stop, or minutes.'
            cur = current.get(int(step_id))
            if cur and self._step_fields_unchanged(cur, fields):
                continue
            ok, msg = self.ui_program_update_single_step(
                program_id,
                int(step_id),
                float(fields['t_start']),
                float(fields['t_stop']),
                float(fields['minutes']),
            )
            if not ok:
                return msg
        return None

    def update_new_program_draft_step(self, step_id: int, t_start: float, t_stop: float, minutes: float) -> None:
        with self._lock:
            for row in self._new_program_draft:
                if int(row[0]) == int(step_id):
                    row[1] = float(t_start)
                    row[2] = float(t_stop)
                    row[3] = float(minutes)
                    return

    def update_new_program_draft_from_form(self, step_updates: Dict[int, Dict[str, float]]) -> None:
        if not step_updates:
            return
        for step_id, fields in step_updates.items():
            if len(fields) >= 3:
                self.update_new_program_draft_step(
                    int(step_id),
                    float(fields['t_start']),
                    float(fields['t_stop']),
                    float(fields['minutes']),
                )

    def ui_program_edit_save(
        self,
        description: str,
        mode: float,
        enabled_freqs: List[str],
        range_max: float,
        step_updates: Optional[Dict[int, Dict[str, float]]] = None,
    ) -> str:
        if not self._db_available():
            return self._database_unavailable_message()
        program_id = self._active_program_id()
        if program_id <= 0:
            return 'No program loaded.'
        try:
            step_err = self.ui_program_edit_save_steps(program_id, step_updates or {})
            if step_err:
                return f'ERROR: {step_err}'
            meta_resp = self._db_query({
                'cmd': 'set_program_meta',
                'program_id': program_id,
                'key': 'description',
                'value': str(description or '').strip(),
            })
            if meta_resp.get('result') != 'Ok':
                return f'ERROR: {meta_resp.get("error", "description save failed")}'
            freqs = {int(float(x)) for x in (enabled_freqs or [])} or {1000}
            config = E720SweepConfig(
                mode=int(mode),
                enabled_frequencies=freqs,
                range_min_hz=float(min(freqs)),
                range_max_hz=float(range_max),
            )
            response = self._db_query({'cmd': 'set_e720', **config.to_db_payload(program_id)})
            if response.get('result') != 'Ok':
                err = response.get('error') or 'E7-20 config save failed'
                return f'ERROR: {err}'
            self._load_e720_config_for_program(program_id)
            return f'Program {program_id} saved.'
        except Exception as exc:
            return f'ERROR: {exc}'

    def ui_program_edit_add_step(self, t_start: float, t_stop: float, minutes: float) -> Tuple[Any, str]:
        program_id = self._active_program_id()
        if program_id <= 0:
            return [], 'No program loaded.'
        try:
            response = self._db_query({
                'cmd': 'program_step_insert',
                'program_id': program_id,
                't_start': float(t_start),
                't_stop': float(t_stop),
                'minutes': float(minutes),
            })
            if response.get('result') != 'Ok':
                return self._steps_table(program_id), f'ERROR: {response.get("error", "unknown")}'
            return self._steps_table(program_id), 'Step added.'
        except Exception as exc:
            return [], f'ERROR: {exc}'

    def ui_program_edit_delete_step(self, step_id: float) -> Tuple[Any, str]:
        program_id = self._active_program_id()
        if program_id <= 0:
            return [], 'No program loaded.'
        try:
            response = self._db_query({'cmd': 'program_delete_temp', 'id': int(step_id)})
            if response.get('result') != 'Ok':
                return self._steps_table(program_id), f'ERROR: {response.get("error", "unknown")}'
            return self._steps_table(program_id), f'Removed step {int(step_id)}.'
        except Exception as exc:
            return [], f'ERROR: {exc}'

    def ui_start_program_from_edit(self) -> Tuple[str, str]:
        program_id = self._active_program_id()
        msg, banner = self.ui_start_program(float(program_id))
        return banner, msg

    def ui_stop_program_from_edit(self) -> Tuple[str, str]:
        msg, banner = self.ui_stop_program()
        return banner, msg

    def launch_web(self) -> None:
        import uvicorn

        from webui.web_app import create_app

        app = create_app(self)
        self._log(
            f'Web UI at http://{self.bind_host}:{self.bind_port} '
            f'(auth_enabled={self.auth_enabled}, live: /ws/dashboard, /api/dashboard/snapshot)'
        )
        uvicorn.run(app, host=self.bind_host, port=self.bind_port, log_level='info')


def main(args: Optional[List[str]] = None) -> None:
    rclpy.init(args=args)
    node = WebHMINode()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        node.launch_web()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            executor.shutdown()
        except Exception:
            pass
        try:
            node.destroy_node()
        except Exception:
            pass
        if rclpy.ok():
            try:
                rclpy.shutdown()
            except Exception:
                pass
        thread.join(timeout=2.0)


if __name__ == '__main__':
    main()
