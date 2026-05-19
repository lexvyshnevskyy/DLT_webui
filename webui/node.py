from __future__ import annotations

import json
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

import gradio as gr
import rclpy
from database.srv import Query as DatabaseQuery
from msgs.msg import E720, Measurement
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import UInt8

from webui.collectors import host_stats, network_config, network_info, serial_ports, systemd_ops
from webui.e720_sweep import E720SweepConfig, E720SweepController, STANDARD_FREQUENCIES, SWEEP_MODE_LABELS
from webui.e720_view import e720_from_msg, e720_summary_text, e720_table_row
from webui.experiment_runner import ExperimentRunner, ExperimentState, ProgramStep
from webui.export_data import export_program_archive
from webui.measurement_log import build_measurement_row, insert_measurement
from webui.system_config import (
    get_configuration_snapshot,
    restart_service,
    serial_port_choices,
    write_env_file,
)
from webui.ui_app import build_ui

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
        self.declare_parameter('bind_port', 7860)
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
        self.auth_enabled = bool(self.get_parameter('auth_enabled').value)
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
        self.create_subscription(Measurement, self.measurement_topic, self._on_measurement, 100)
        self.create_subscription(E720, self.measure_topic, self._on_e720, 10)

        self._runner = ExperimentRunner()
        self._sweep = E720SweepController()
        self._experiment = ExperimentState()

        self._lock = threading.RLock()
        self._latest_measurements: Dict[int, Dict[str, Any]] = {}
        self._latest_e720: Optional[E720] = None
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

    def _on_measurement(self, msg: Measurement) -> None:
        with self._lock:
            self._latest_measurements[int(msg.channel)] = {
                'channel': int(msg.channel),
                'type': str(msg.type),
                'value': float(msg.value),
                'valid': bool(msg.valid),
                'updated_monotonic': time.monotonic(),
            }

    def _on_e720(self, msg: E720) -> None:
        with self._lock:
            self._latest_e720 = msg

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

    def _measurements_table(self) -> List[List[Any]]:
        now = time.monotonic()
        with self._lock:
            items = sorted(self._latest_measurements.items())
        return [
            [ch, item['type'], item['value'], item['valid'], round(max(0.0, now - item['updated_monotonic']), 3)]
            for ch, item in items
        ]

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

    def _temperature_summary(self) -> str:
        status = self._critical_services_status()
        tc = self._last_core_snapshot.get('temperature_control') or {}
        lines = [
            f'ROS ready: {status.get("system_ready")}',
            f'Control enabled: {tc.get("enabled", "—")}',
            f'Target K: {tc.get("target_k", "—")}',
            f'Heater output: {tc.get("heater_output", "—")}',
            f'Control temp K: {tc.get("latest_control_temp_k", "—")}',
            f'Monitor temp K: {tc.get("latest_monitor_temp_k", "—")}',
            f'Measurement logging: {self.enable_measurement_logging}',
        ]
        if status.get('pwm_note'):
            lines.append(str(status['pwm_note']))
        return '\n'.join(str(x) for x in lines)

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
        e720_data = e720_from_msg(e720_msg)
        core_json = json.dumps(self._last_core_snapshot.get('temperature_control') or {}, indent=2)
        return (
            self._experiment_banner(),
            self._temperature_summary(),
            self._measurements_table(),
            e720_summary_text(e720_data),
            [e720_table_row(e720_data)],
            core_json,
        )

    def ui_tick_network(self) -> List[List[Any]]:
        return network_info.interfaces_table()

    def _save_env_section(
        self,
        updates: Dict[str, str],
        service: str,
        restart: bool,
    ) -> str:
        ok, msg = write_env_file(self.delatometry_env_file, updates)
        if not ok:
            return f'Failed to write {self.delatometry_env_file}: {msg}'
        self._log(f'Configuration updated: {", ".join(updates.keys())}')
        if not restart:
            return f'Saved ({msg}). Restart {service} to apply.'
        ok_r, msg_r = restart_service(service)
        if service.endswith('webui.service'):
            return f'Saved ({msg}). Skipped webui restart — refresh page or restart from SSH.'
        if ok_r:
            return f'Saved and {msg_r}.'
        return f'Saved ({msg}) but restart failed: {msg_r}'

    def ui_load_configuration(self) -> Tuple[Any, ...]:
        snap = get_configuration_snapshot(self.delatometry_env_file)
        ltm = snap['ltm2985']
        meas = snap['measure_device']
        db = snap['database']
        core = snap['core']
        ads = snap['ads1256']
        status = f'Loaded {self.delatometry_env_file}' if snap.get('env_readable') else (
            f'Warning: could not read {self.delatometry_env_file} (using defaults)'
        )
        nm_choices = network_info.list_nmcli_connections()
        return (
            status,
            network_info.interfaces_table(),
            gr.update(choices=nm_choices, value=nm_choices[0] if nm_choices else None),
            gr.update(choices=serial_port_choices(ltm['port']), value=ltm['port']),
            float(ltm['baudrate']),
            gr.update(choices=serial_port_choices(meas['port']), value=meas['port']),
            float(meas['speed']),
            db['host'],
            float(db['port']),
            db['name'],
            db['user'],
            db['password'],
            db['auto_init_schema'],
            core['namespace'],
            core['measurement_topic'],
            core['enable_database_client'],
            core['enable_pwm_controller'],
            ads['simulate'],
            ads['fallback_to_simulation'],
        )

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
        namespace: str,
        measurement_topic: str,
        enable_db_client: bool,
        enable_pwm: bool,
        restart: bool,
    ) -> Tuple[str, str]:
        msg = self._save_env_section(
            {
                'DELATOMETRY_CORE_NAMESPACE': str(namespace).strip().strip('/'),
                'DELATOMETRY_CORE_MEASUREMENT_TOPIC': str(measurement_topic).strip(),
                'DELATOMETRY_CORE_ENABLE_DATABASE_CLIENT': 'true' if enable_db_client else 'false',
                'DELATOMETRY_CORE_ENABLE_PWM_CONTROLLER': 'true' if enable_pwm else 'false',
            },
            'delatometry-core.service',
            bool(restart),
        )
        return msg, msg

    def ui_save_ads1256_config(self, simulate: bool, fallback: bool, restart: bool) -> Tuple[str, str]:
        msg = self._save_env_section(
            {
                'DELATOMETRY_ADS1256_SIMULATE': 'true' if simulate else 'false',
                'DELATOMETRY_ADS1256_FALLBACK_TO_SIMULATION': 'true' if fallback else 'false',
            },
            'delatometry-ads1256.service',
            bool(restart),
        )
        return msg, msg

    def ui_service_control(self, unit: str, action: str) -> str:
        if not self.enable_service_control:
            return 'Service control disabled (enable_service_control:=true).'
        if 'delatometry-webui.service' in str(unit) and action == 'restart':
            return 'Restart webui from SSH to avoid dropping this browser session.'
        result = systemd_ops.control_unit(str(unit), action, use_sudo=True)
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

    def ui_refresh_nm_connections(self):
        choices = network_info.list_nmcli_connections()
        return gr.update(choices=choices, value=choices[0] if choices else None)

    def ui_apply_static_ip(self, connection: str, address: str, prefix: float, gateway: str, dns: str) -> str:
        result = network_config.set_ipv4(
            str(connection), str(address).strip(), int(prefix), str(gateway), str(dns),
            use_sudo=self.network_use_sudo,
        )
        msg = 'OK' if result.get('ok') else result.get('error', 'failed')
        self._log(f'Static IP {connection}: {msg}')
        return msg

    def ui_apply_dhcp(self, connection: str) -> str:
        result = network_config.set_dhcp(str(connection), use_sudo=self.network_use_sudo)
        msg = 'OK' if result.get('ok') else result.get('error', 'failed')
        self._log(f'DHCP {connection}: {msg}')
        return msg

    def ui_wifi_scan(self) -> Tuple[List[List[Any]], str]:
        result = network_config.wifi_scan()
        if not result.get('ok'):
            return [], result.get('error', 'scan failed')
        rows = result.get('rows', [])
        return rows, f'Found {len(rows)} network(s).'

    def ui_wifi_connect(self, ssid: str, password: str, interface: str) -> str:
        result = network_config.wifi_connect(ssid, password, str(interface), use_sudo=self.network_use_sudo)
        msg = 'Connected' if result.get('ok') else result.get('error', 'failed')
        self._log(f'Wi-Fi {ssid}: {msg}')
        return msg

    def build_ui(self) -> gr.Blocks:
        return build_ui(self)

    def launch_ui(self) -> None:
        demo = self.build_ui()
        launch_kwargs: Dict[str, Any] = {
            'server_name': self.bind_host,
            'server_port': self.bind_port,
            'show_error': True,
            'prevent_thread_lock': False,
            'share': False,
            'auth': None,
        }
        if self.auth_enabled:
            launch_kwargs['auth'] = (self.auth_user, self.auth_password)
        if self.queue_enabled:
            demo.queue()
        self._log(f'UI at http://{self.bind_host}:{self.bind_port}')
        demo.launch(**launch_kwargs)


def main(args: Optional[List[str]] = None) -> None:
    rclpy.init(args=args)
    node = WebHMINode()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        node.launch_ui()
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()
        thread.join(timeout=2.0)


if __name__ == '__main__':
    main()
