from __future__ import annotations

import json
import queue
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

import rclpy
from database.srv import Query as DatabaseQuery
from msgs.msg import Ads, E720, Measurement
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import String, UInt8

from webui.collectors import db_test, gpio_pins, host_stats, network_config, network_info, serial_ports, systemd_ops, vpn_config
from webui.param_utils import ros_param_bool
from webui.program_steps import parse_step_field_updates
from webui.temperature_validation import (
    EXPERIMENT_MODE_LABELS,
    normalize_experiment_mode,
    suggest_next_step,
    validate_new_program,
    validate_temperature_steps,
)
from webui.e720_sweep import E720SweepConfig, E720SweepController, STANDARD_FREQUENCIES, SWEEP_MODE_LABELS
from webui.e720_view import e720_from_msg, e720_summary_text, e720_table_row
from webui.program_schedule import IDLE_EXPERIMENT_TIMING, ProgramStep, parse_program_steps
from webui.export_data import export_program_archive, export_run_archive
from webui.run_charts import delete_run_charts, freq_chart_filename, generate_run_charts, run_chart_dir
from webui.measure_source import resolve_measure_topics_from_env
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


@dataclass
class _PendingServiceCall:
    client: Any
    request_type: Any
    service_name: str
    payload: Dict[str, Any]
    timeout_sec: float
    done: threading.Event
    result: Optional[Dict[str, Any]] = None
    error: Optional[BaseException] = None


CoreQuery = DatabaseQuery

DEFAULT_SYSTEMD_UNITS = [
    'delatometry-database.service',
    'delatometry-ltm2985.service',
    'delatometry-measure-device.service',
    'delatometry-im3536.service',
    'delatometry-ads1256.service',
    'delatometry-core.service',
    'delatometry-hmi.service',
    'delatometry-webui.service',
]


class WebHMINode(Node):
    def __init__(self) -> None:
        super().__init__('webui')

        self.declare_parameter('core_service', '/core/query')
        self.declare_parameter('experiment_status_topic', '/core/experiment/status')
        self.declare_parameter('database_service', '/database/query')
        self.declare_parameter('measurement_topic', '/ltm2985/measurement')
        self.declare_parameter('measure_topic', '/measure_device')
        self.declare_parameter('measure_command_topic', '/measure_device/command')
        self.declare_parameter('measure_source', 'e720')
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
        self.declare_parameter('run_charts_dir', '')
        self.declare_parameter('enable_service_control', True)
        self.declare_parameter('network_use_sudo', True)
        self.declare_parameter('ros_service_timeout_sec', 15.0)
        self.declare_parameter('database_list_timeout_sec', 30.0)
        self.declare_parameter('programs_cache_refresh_sec', 3.0)
        self.declare_parameter('core_service_timeout_sec', 3.0)
        self.declare_parameter('ltm_raw_topic', '/ltm2985/raw_json')
        self.declare_parameter('ads_topic', '/ads1256')
        self.declare_parameter('stream_max_lines', 30)
        self.declare_parameter('ltm_control_channel', 9)
        self.declare_parameter('ltm_monitor_channel', 3)
        self.declare_parameter('systemd_units', DEFAULT_SYSTEMD_UNITS)

        self.core_service = str(self.get_parameter('core_service').value)
        self.experiment_status_topic = str(self.get_parameter('experiment_status_topic').value)
        self.database_service = str(self.get_parameter('database_service').value)
        self.measurement_topic = str(self.get_parameter('measurement_topic').value)
        self.delatometry_env_file = str(self.get_parameter('delatometry_env_file').value)
        measure_topics = resolve_measure_topics_from_env(read_env_file(self.delatometry_env_file))
        self.measure_source = measure_topics['source']
        self.measure_topic = measure_topics['measure_topic']
        self.measure_command_topic = measure_topics['measure_command_topic']
        self.bind_host = str(self.get_parameter('bind_host').value)
        self.bind_port = int(self.get_parameter('bind_port').value)
        self.title = str(self.get_parameter('title').value)
        self.queue_enabled = bool(self.get_parameter('queue_enabled').value)
        self.auth_enabled = ros_param_bool(self.get_parameter('auth_enabled').value)
        self.auth_user = str(self.get_parameter('auth_user').value)
        self.auth_password = str(self.get_parameter('auth_password').value)
        self.status_refresh_period_sec = float(self.get_parameter('status_refresh_period_sec').value)
        self.control_loop_period_sec = float(self.get_parameter('control_loop_period_sec').value)
        self.enable_service_control = bool(self.get_parameter('enable_service_control').value)
        self.network_use_sudo = bool(self.get_parameter('network_use_sudo').value)
        self.ros_service_timeout_sec = max(2.0, float(self.get_parameter('ros_service_timeout_sec').value))
        self.database_list_timeout_sec = max(
            5.0,
            float(self.get_parameter('database_list_timeout_sec').value),
        )
        self.programs_cache_refresh_sec = max(
            1.0,
            float(self.get_parameter('programs_cache_refresh_sec').value),
        )
        self.core_service_timeout_sec = max(1.0, float(self.get_parameter('core_service_timeout_sec').value))
        self._ros_executor_thread_id: Optional[int] = None
        self._pending_service_calls: queue.Queue = queue.Queue(maxsize=128)
        self._dispatch_cb_group = ReentrantCallbackGroup()
        self._service_dispatch_timer = self.create_timer(
            0.01,
            self._dispatch_pending_service_calls,
            callback_group=self._dispatch_cb_group,
        )
        self.ltm_raw_topic = str(self.get_parameter('ltm_raw_topic').value)
        self.ads_topic = str(self.get_parameter('ads_topic').value)
        self.stream_max_lines = int(self.get_parameter('stream_max_lines').value)
        self.ltm_control_channel = int(self.get_parameter('ltm_control_channel').value)
        self.ltm_monitor_channel = int(self.get_parameter('ltm_monitor_channel').value)

        export_dir = str(self.get_parameter('export_dir').value).strip()
        self.export_dir = export_dir or str(Path(tempfile.gettempdir()) / 'delatometry_exports')
        charts_dir = str(self.get_parameter('run_charts_dir').value).strip()
        self.run_charts_dir = charts_dir or str(Path(tempfile.gettempdir()) / 'delatometry_run_charts')
        Path(self.run_charts_dir).mkdir(parents=True, exist_ok=True)

        units_param = self.get_parameter('systemd_units').value
        self.systemd_units = (
            [str(u) for u in units_param]
            if isinstance(units_param, (list, tuple)) and units_param
            else list(DEFAULT_SYSTEMD_UNITS)
        )

        self._service_cb_group = self._dispatch_cb_group
        self.core_client = self.create_client(
            CoreQuery,
            self.core_service,
            callback_group=self._service_cb_group,
        )
        self.db_client = self.create_client(
            DatabaseQuery,
            self.database_service,
            callback_group=self._service_cb_group,
        )
        self._e720_cmd_pub = self.create_publisher(UInt8, self.measure_command_topic, 10)
        stream_len = max(10, self.stream_max_lines)
        self.create_subscription(Measurement, self.measurement_topic, self._on_measurement, 100)
        self.create_subscription(E720, self.measure_topic, self._on_e720, 10)
        self.create_subscription(String, self.ltm_raw_topic, self._on_ltm_raw, 20)
        self.create_subscription(Ads, self.ads_topic, self._on_ads, 10)
        self.create_subscription(String, self.experiment_status_topic, self._on_core_experiment_status, 10)

        self._sweep = E720SweepController()
        self._sweep_loaded_program_id: Optional[int] = None
        self._core_program_status: Dict[str, Any] = {}
        self._program_was_running: bool = False
        self._last_active_run: Optional[Tuple[int, int]] = None

        self._lock = threading.RLock()
        self._programs_nav_program_id = 0
        self._new_program_draft: List[List[Any]] = []
        self._new_program_description: str = ''
        self._new_program_sweep_mode: int = 0
        self._new_program_experiment_mode: str = 'default'
        self._new_program_enabled_freqs: List[str] = ['1000']
        self._new_program_range_max: float = 10000.0
        self._last_topic_peek: Dict[str, str] = {}
        self._last_wifi_scan: List[List[Any]] = []
        self._last_db_test_msg = ''
        self._latest_measurements: Dict[int, Dict[str, Any]] = {}
        self._latest_e720: Optional[E720] = None
        self._latest_e720_monotonic: float = 0.0
        self._latest_ads: Optional[Ads] = None
        self._latest_ltm_raw: Optional[str] = None
        self._ltm_stream: Deque[str] = deque(maxlen=stream_len)
        self._e720_stream: Deque[str] = deque(maxlen=stream_len)
        self._last_core_snapshot: Dict[str, Any] = {}
        self._log_lines: Deque[str] = deque(maxlen=300)
        self._last_service_log_time: Dict[str, float] = {}
        self._programs_cache_rows: List[List[Any]] = []
        self._programs_cache_error: str = ''
        self._programs_cache_updated_monotonic: float = 0.0
        self._programs_cache_refreshing = False
        self._programs_cache_timer = self.create_timer(
            self.programs_cache_refresh_sec,
            self._refresh_programs_cache,
        )
        self._control_timer = self.create_timer(self.control_loop_period_sec, self._control_tick)
        self._bootstrap_done = False
        self._bootstrap_timer = self.create_timer(2.0, self._bootstrap_active_program_state)
        self._log('webui node started')
        self._refresh_programs_cache()

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
            self._latest_e720_monotonic = time.monotonic()
            self._e720_stream.appendleft(line)

    def _on_ltm_raw(self, msg: String) -> None:
        with self._lock:
            self._latest_ltm_raw = str(msg.data)

    def _on_ads(self, msg: Ads) -> None:
        with self._lock:
            self._latest_ads = msg

    def _on_core_experiment_status(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data or '{}')
        except json.JSONDecodeError:
            return
        program = payload.get('program') or {}
        tc = payload.get('temperature_control')
        pwm = payload.get('pwm')
        ended: Optional[Tuple[int, int]] = None
        with self._lock:
            prev_pid = self._core_program_status.get('program_id')
            prev_run = self._core_program_status.get('run_id')
            self._core_program_status = dict(program)
            incoming: Dict[str, Any] = {'result': 'Ok', 'program': program}
            if tc is not None:
                incoming['temperature_control'] = tc
            if pwm is not None:
                incoming['pwm'] = pwm
            self._last_core_snapshot = self._merge_core_snapshot(incoming)
            new_pid = program.get('program_id')
            if new_pid is None and prev_pid is not None and prev_run is not None:
                ended = (int(prev_pid), int(prev_run))
        if ended is not None:
            pid, rid = ended
            self._schedule_run_charts_on_finish(pid, rid)
            self._clear_e720_sweep_runtime()
        elif program.get('program_id') is not None:
            self._ensure_e720_sweep_for_running_program()

    def _is_program_running(self) -> bool:
        with self._lock:
            return self._core_program_status.get('program_id') is not None

    def _on_ros_executor_thread(self) -> bool:
        tid = self._ros_executor_thread_id
        return tid is not None and threading.get_ident() == tid

    def _call_service_json_on_executor(
        self,
        client: Any,
        request_type: Any,
        service_name: str,
        payload: Dict[str, Any],
        timeout_sec: float,
    ) -> Dict[str, Any]:
        if not client.service_is_ready():
            if not client.wait_for_service(timeout_sec=min(1.0, float(timeout_sec))):
                raise RuntimeError(f'Service is not available: {service_name}')
        request = request_type()
        request.query = json.dumps(payload)
        future = client.call_async(request)
        # rclpy.task.Future.result() is not concurrent.futures.Future.result():
        # on some ROS 2/Python versions it does not accept a timeout argument.
        # Also, this method may run inside one MultiThreadedExecutor worker while
        # another executor worker completes the client response.  Therefore wait
        # by polling future.done() with a monotonic deadline.
        deadline = time.monotonic() + max(0.1, float(timeout_sec))
        while not future.done() and time.monotonic() < deadline:
            time.sleep(0.005)
        if not future.done():
            try:
                future.cancel()
            except Exception:
                pass
            raise TimeoutError(f'Timeout waiting for {service_name}')
        try:
            response = future.result()
        except Exception as exc:
            raise RuntimeError(f'Service call failed: {service_name}: {exc}') from exc
        if response is None:
            raise RuntimeError(f'Service call failed: {service_name}')
        return json.loads(response.response or '{}')

    def _dispatch_pending_service_calls(self) -> None:
        processed = 0
        while processed < 32:
            try:
                pending = self._pending_service_calls.get_nowait()
            except queue.Empty:
                break
            processed += 1
            try:
                pending.result = self._call_service_json_on_executor(
                    pending.client,
                    pending.request_type,
                    pending.service_name,
                    pending.payload,
                    pending.timeout_sec,
                )
            except BaseException as exc:
                pending.error = exc
            finally:
                pending.done.set()

    def _invoke_service_json(
        self,
        client: Any,
        request_type: Any,
        service_name: str,
        payload: Dict[str, Any],
        timeout_sec: Optional[float] = None,
    ) -> Dict[str, Any]:
        if timeout_sec is None:
            timeout_sec = self.ros_service_timeout_sec
        if self._on_ros_executor_thread():
            return self._call_service_json_on_executor(
                client,
                request_type,
                service_name,
                payload,
                float(timeout_sec),
            )
        pending = _PendingServiceCall(
            client=client,
            request_type=request_type,
            service_name=service_name,
            payload=payload,
            timeout_sec=float(timeout_sec),
            done=threading.Event(),
        )
        try:
            self._pending_service_calls.put_nowait(pending)
        except queue.Full:
            raise RuntimeError(f'Service call queue full ({service_name})')
        wait_sec = float(timeout_sec) + 2.0
        if not pending.done.wait(timeout=wait_sec):
            raise TimeoutError(f'Timeout waiting for {service_name}')
        if pending.error is not None:
            raise pending.error
        if pending.result is None:
            raise RuntimeError(f'Service call failed: {service_name}')
        return pending.result

    def _db_query(self, payload: Dict[str, Any], *, timeout_sec: Optional[float] = None) -> Dict[str, Any]:
        if timeout_sec is None:
            cmd = str(payload.get('cmd', ''))
            if cmd in ('program_all_list', 'program_all_list_with_counts', 'program_run_counts'):
                timeout_sec = self.database_list_timeout_sec
            else:
                timeout_sec = self.ros_service_timeout_sec
        return self._invoke_service_json(
            self.db_client,
            DatabaseQuery.Request,
            self.database_service,
            payload,
            timeout_sec=timeout_sec,
        )

    def _merge_core_snapshot(self, incoming: Dict[str, Any]) -> Dict[str, Any]:
        """Merge a /core/query response into cached state without dropping pwm/tc sections."""
        merged = dict(self._last_core_snapshot)
        for key in (
            'result',
            'error',
            'program_scheduler_note',
            'hmi_published',
            'database_service_ready',
        ):
            if key in incoming:
                merged[key] = incoming[key]
        if 'measurements' in incoming:
            merged['measurements'] = incoming['measurements']
        program = incoming.get('program')
        if program is not None:
            merged['program'] = program
            self._core_program_status = dict(program)
        tc = incoming.get('temperature_control')
        if tc is not None:
            merged['temperature_control'] = tc
        pwm = incoming.get('pwm')
        if pwm is not None:
            merged['pwm'] = pwm
        return merged

    def _core_query(self, payload: Dict[str, Any], *, timeout_sec: Optional[float] = None) -> Dict[str, Any]:
        if timeout_sec is None:
            if payload.get('program') is not None:
                timeout_sec = max(self.core_service_timeout_sec, 30.0)
            else:
                timeout_sec = self.core_service_timeout_sec
        result = self._invoke_service_json(
            self.core_client,
            CoreQuery.Request,
            self.core_service,
            payload,
            timeout_sec=timeout_sec,
        )
        with self._lock:
            self._last_core_snapshot = self._merge_core_snapshot(result)
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
        return parse_program_steps(response.get('row', []))

    def _load_e720_config_for_program(self, program_id: int) -> None:
        if program_id <= 0 or not self._db_available():
            return
        try:
            response = self._db_query({'cmd': 'get_e720', 'id': program_id})
            if response.get('result') == 'Ok' and response.get('row'):
                self._sweep.load_config(E720SweepConfig.from_db_row(response['row']))
        except Exception as exc:
            self._log(f'Could not load E7-20 config for program {program_id}: {exc}')

    def _clear_e720_sweep_runtime(self) -> None:
        self._sweep.reset()
        with self._lock:
            self._sweep_loaded_program_id = None

    def _ensure_e720_sweep_for_running_program(self) -> None:
        """Load E7-20 sweep from DB when core runs a program (start, restart, or HMI)."""
        with self._lock:
            raw_pid = self._core_program_status.get('program_id')
            loaded_pid = self._sweep_loaded_program_id
        if raw_pid is None:
            if loaded_pid is not None:
                self._clear_e720_sweep_runtime()
            return
        program_id = int(raw_pid)
        if program_id <= 0 or loaded_pid == program_id:
            return
        self._load_e720_config_for_program(program_id)
        with self._lock:
            self._sweep_loaded_program_id = program_id
        self._log(f'Loaded E7-20 sweep for active program {program_id}')

    def _bootstrap_active_program_state(self) -> None:
        if self._bootstrap_done:
            return
        self._bootstrap_done = True
        try:
            self._bootstrap_timer.cancel()
        except Exception:
            pass
        self._sync_core_program_status()
        self._ensure_e720_sweep_for_running_program()

    def _publish_e720_byte(self, byte_val: int) -> None:
        if self.measure_source != 'e720':
            return
        msg = UInt8()
        msg.data = int(byte_val) & 0xFF
        self._e720_cmd_pub.publish(msg)

    def _schedule_run_charts_on_finish(self, program_id: int, run_id: int) -> None:
        if program_id <= 0 or run_id <= 0:
            return
        self._schedule_run_charts(run_id, program_id)

    def _schedule_run_charts(self, run_id: int, program_id: int) -> None:
        def _worker() -> None:
            try:
                result = generate_run_charts(self._db_query, self.run_charts_dir, run_id, program_id)
                if result.get('ok'):
                    self._log(f'Charts saved for run {program_id}.{run_id}: {result.get("charts", [])}')
                else:
                    self._log(f'Chart generation for run {run_id}: {result.get("error", "failed")}')
            except Exception as exc:
                self._log(f'Chart generation for run {run_id} failed: {exc}')

        threading.Thread(target=_worker, daemon=True, name=f'run-charts-{run_id}').start()

    def run_chart_file_path(self, program_id: int, run_id: int, name: str) -> Optional[Path]:
        safe = Path(name).name
        if safe != name or '..' in name:
            return None
        path = run_chart_dir(Path(self.run_charts_dir), program_id, run_id) / safe
        return path if path.is_file() else None

    def ui_delete_program_run(self, program_id: int, run_id: int) -> str:
        if not self._db_available():
            return self._database_unavailable_message()
        program_id_int = int(program_id)
        run_id_int = int(run_id)
        with self._lock:
            active_run = self._core_program_status.get('run_id')
            if active_run is not None and int(active_run) == run_id_int:
                return 'Stop the experiment before deleting this run.'
        try:
            detail = self._db_query({'cmd': 'program_run_get', 'run_id': run_id_int})
            if detail.get('result') != 'Ok':
                return 'Experiment run not found.'
            row = detail.get('row') or {}
            if int(row.get('program_id', 0)) != program_id_int:
                return 'Run does not belong to this program.'
            delete_run_charts(self.run_charts_dir, program_id_int, run_id_int)
            response = self._db_query({'cmd': 'program_run_delete', 'run_id': run_id_int})
            if response.get('result') != 'Ok':
                return f'ERROR: {response.get("error", "delete failed")}'
            self._log(f'Deleted experiment run {row.get("label", run_id_int)}')
            return f'Deleted experiment {row.get("label", run_id_int)} and all measurements.'
        except Exception as exc:
            return f'ERROR: {exc}'

    def program_run_view_fields(self, program_id: int, run_id: int) -> Dict[str, Any]:
        pid = int(program_id or 0)
        rid = int(run_id or 0)
        empty: Dict[str, Any] = {
            'run': {},
            'temperature_chart_url': None,
            'freq_tabs': [],
            'charts_ready': False,
            'message': 'Invalid run.',
        }
        if not self._db_available() or pid <= 0 or rid <= 0:
            return empty
        try:
            detail = self._db_query({'cmd': 'program_run_get', 'run_id': rid})
            if detail.get('result') != 'Ok':
                empty['message'] = detail.get('error', 'not found')
                return empty
            run = detail.get('row') or {}
            if int(run.get('program_id', 0)) != pid:
                empty['message'] = 'Run does not belong to this program.'
                return empty
            chart_base = f'/program-run/chart?program_id={pid}&run_id={rid}&name='
            chart_dir = run_chart_dir(Path(self.run_charts_dir), pid, rid)
            temp_name = 'temperature.png'
            temp_path = chart_dir / temp_name
            temperature_chart_url = chart_base + temp_name if temp_path.is_file() else None
            freqs_resp = self._db_query({'cmd': 'measurement_run_frequencies', 'run_id': rid})
            freqs = freqs_resp.get('row', []) if freqs_resp.get('result') == 'Ok' else []
            freq_tabs: List[Dict[str, Any]] = []
            for freq in freqs:
                value = float(freq)
                fname = freq_chart_filename(value)
                chart_url = chart_base + fname if (chart_dir / fname).is_file() else None
                if abs(value) < 1e-9:
                    label = 'E7-20 offline (0 Hz)'
                else:
                    label = f'{value:g} Hz'
                freq_tabs.append({
                    'freq': value,
                    'label': label,
                    'chart_url': chart_url,
                    'filename': fname,
                })
            stats = run.get('measurement_stats') or {}
            with self._lock:
                active_run = self._core_program_status.get('run_id')
            is_active = active_run is not None and int(active_run) == rid
            charts_ready = temperature_chart_url is not None or any(t.get('chart_url') for t in freq_tabs)
            return {
                'run': run,
                'temperature_chart_url': temperature_chart_url,
                'freq_tabs': freq_tabs,
                'charts_ready': charts_ready,
                'is_active': is_active,
                'sample_count': int(stats.get('count', 0) or 0),
                'message': f'Experiment {run.get("label", rid)}.',
            }
        except Exception as exc:
            empty['message'] = f'ERROR: {exc}'
            return empty

    def _finish_program_runs_for_program(self, program_id: int, final_status: str = 'Stopped') -> None:
        if not self._db_available():
            return
        try:
            self._db_query({
                'cmd': 'program_run_finish_active',
                'program_id': int(program_id),
                'status': final_status,
            })
        except Exception as exc:
            self._log(f'Failed to finish active runs for program {program_id}: {exc}')

    def _refresh_core_live_state(self) -> None:
        """Pull pwm + temperature_control from core (runs on ROS executor timer)."""
        if not self._core_available():
            return
        try:
            response = self._core_query({}, timeout_sec=min(2.0, self.core_service_timeout_sec))
            if str(response.get('result', '')).lower() not in ('ok', 'true'):
                return
        except Exception:
            pass

    def _control_tick(self) -> None:
        try:
            self._refresh_core_live_state()
            finished_run: Optional[Tuple[int, int]] = None
            with self._lock:
                prog = self._core_program_status
                running = prog.get('program_id') is not None
                e720_data = e720_from_msg(self._latest_e720)
                was_running = self._program_was_running
                self._program_was_running = running
                if running:
                    pid = int(prog['program_id'])
                    rid = prog.get('run_id')
                    if rid is not None:
                        self._last_active_run = (pid, int(rid))
                elif was_running and self._last_active_run is not None:
                    finished_run = self._last_active_run
                    self._last_active_run = None

            if finished_run is not None:
                self._clear_e720_sweep_runtime()
                self._schedule_run_charts_on_finish(finished_run[0], finished_run[1])
            elif was_running and not running:
                self._clear_e720_sweep_runtime()

            if running and self.measure_source == 'e720':
                self._ensure_e720_sweep_for_running_program()
                sweep_result = self._sweep.tick(float(e720_data.get('frequency', 0) or 0), running=True)
                cmd = sweep_result.get('command_byte')
                if cmd is not None:
                    self._publish_e720_byte(int(cmd))
        except Exception as exc:
            self._log(f'Control loop error: {exc}')

    def _halt_temperature_control(self, reason: str = '') -> None:
        """Stop PI / PWM stabilize mode on core."""
        if not self._core_available():
            return
        try:
            self._core_query({'temperature_control': {'enabled': False}})
            if reason:
                self._log(f'Temperature control stopped ({reason})')
        except Exception as exc:
            self._log(f'Failed to disable control: {exc}')

    def _mark_programs_stopped_in_db(self, except_program_id: Optional[int] = None) -> List[int]:
        """Set Status=Stopped for every program marked Running in the database."""
        stopped: List[int] = []
        if not self._db_available():
            return stopped
        try:
            response = self._db_query({'cmd': 'program_all_list'})
            if response.get('result') != 'Ok':
                return stopped
            for row in response.get('row', []):
                if len(row) < 3:
                    continue
                pid = int(row[0])
                status = str(row[2] or '').strip()
                if status.lower() != 'running':
                    continue
                if except_program_id is not None and pid == int(except_program_id):
                    continue
                self._db_query({'cmd': 'program_update_status', 'id': pid, 'status': 'Stopped'})
                stopped.append(pid)
        except Exception as exc:
            self._log(f'Failed to stop running programs in DB: {exc}')
        return stopped

    def _experiment_banner(self) -> str:
        prog = self._core_program_status
        program_id = prog.get('program_id')
        if program_id is None:
            return 'Idle — no program running in core'
        timing = prog.get('timing') or {}
        step = timing.get('step_index', '?')
        total = timing.get('step_count', '?')
        target = prog.get('last_target_k')
        mode = SWEEP_MODE_LABELS.get(self._sweep.config.mode, 'unknown')
        exp_mode = normalize_experiment_mode(str(prog.get('experiment_mode', 'default')))
        exp_label = EXPERIMENT_MODE_LABELS.get(exp_mode, exp_mode)
        label = prog.get('run_label') or program_id
        if exp_mode == 'default':
            return (
                f'RUNNING {label} — step {step}/{total}, '
                f'target {target:.2f} K, E7-20 mode: {mode}'
                if target is not None
                else f'RUNNING {label} — step {step}/{total}, E7-20 mode: {mode}'
            )
        return (
            f'RUNNING {label} — step {step}/{total}, {exp_label}, E7-20 mode: {mode}'
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
        with self._lock:
            prog = self._core_program_status
        if prog.get('program_id') is not None:
            timing = prog.get('timing') or {}
            step = timing.get('step_index', '?')
            total = timing.get('step_count', '?')
            label = prog.get('run_label') or prog.get('program_id')
            lines.append(f'Program {label} running — step {step}/{total}')
        return '\n'.join(lines)

    def _stream_text(self, stream: Deque[str]) -> str:
        with self._lock:
            lines = list(stream)
        return '\n'.join(lines) if lines else '(no messages yet)'

    def _program_run_counts_map(self) -> Dict[int, int]:
        if not self._db_available():
            return {}
        response = self._db_query({'cmd': 'program_run_counts'})
        if response.get('result') != 'Ok':
            return {}
        counts: Dict[int, int] = {}
        for raw_row in response.get('row', []):
            parts = str(raw_row).split('^')
            if len(parts) >= 2:
                counts[int(parts[0])] = int(parts[1])
        return counts

    def _parse_program_all_rows(self, response: Dict[str, Any]) -> List[List[Any]]:
        rows: List[List[Any]] = []
        for raw_row in response.get('row', []):
            parts = str(raw_row).split('^')
            if len(parts) >= 3:
                pid = int(parts[0])
                run_count = int(parts[3]) if len(parts) >= 4 else 0
                rows.append([pid, parts[1], parts[2], run_count])
        return rows

    def _fetch_programs_table_from_db(self) -> Tuple[List[List[Any]], str]:
        if not self._db_available():
            return [], self._database_unavailable_message()
        try:
            response = self._db_query({'cmd': 'program_all_list_with_counts'})
            if response.get('result') != 'Ok':
                err = str(response.get('error', ''))
                if 'No handler' in err or 'program_all_list_with_counts' in err:
                    response = self._db_query({'cmd': 'program_all_list'})
                    if response.get('result') != 'Ok':
                        return [], f'Database error: {response.get("error", "program list failed")}'
                    counts = self._program_run_counts_map()
                    rows = []
                    for raw_row in response.get('row', []):
                        parts = str(raw_row).split('^')
                        if len(parts) >= 3:
                            pid = int(parts[0])
                            rows.append([pid, parts[1], parts[2], counts.get(pid, 0)])
                    return rows, ''
                return [], f'Database error: {err}'
            return self._parse_program_all_rows(response), ''
        except TimeoutError:
            msg = (
                f'Database query timed out ({self.database_service}). '
                'Showing last cached list if available.'
            )
            self._log_throttled('programs_table', msg)
            return [], msg
        except Exception as exc:
            msg = f'Database error: {exc}'
            self._log_throttled('programs_table', msg)
            return [], msg

    def _refresh_programs_cache(self) -> None:
        if self._programs_cache_refreshing:
            return
        self._programs_cache_refreshing = True
        try:
            rows, err = self._fetch_programs_table_from_db()
            with self._lock:
                if rows:
                    self._programs_cache_rows = rows
                    self._programs_cache_error = ''
                elif not self._programs_cache_rows:
                    self._programs_cache_error = err
                else:
                    self._programs_cache_error = err
                self._programs_cache_updated_monotonic = time.monotonic()
        finally:
            self._programs_cache_refreshing = False

    def _programs_table(self, *, force_refresh: bool = False) -> Tuple[List[List[Any]], str]:
        if force_refresh:
            rows, err = self._fetch_programs_table_from_db()
            with self._lock:
                if rows:
                    self._programs_cache_rows = rows
                    self._programs_cache_error = ''
                else:
                    self._programs_cache_error = err
                self._programs_cache_updated_monotonic = time.monotonic()
            return rows, err
        with self._lock:
            rows = list(self._programs_cache_rows)
            err = self._programs_cache_error
            age = time.monotonic() - self._programs_cache_updated_monotonic
        if rows:
            if err and age > self.programs_cache_refresh_sec * 2:
                return rows, f'{err} (showing cached list)'
            return rows, ''
        if age < 1.0 and not err:
            return [], 'Loading programs…'
        rows, err = self._fetch_programs_table_from_db()
        with self._lock:
            if rows:
                self._programs_cache_rows = rows
                self._programs_cache_error = ''
            else:
                self._programs_cache_error = err
            self._programs_cache_updated_monotonic = time.monotonic()
        return rows, err

    def _program_runs_table(self, program_id: int) -> List[Dict[str, Any]]:
        if not self._db_available() or program_id <= 0:
            return []
        response = self._db_query({'cmd': 'program_run_list', 'program_id': program_id})
        if response.get('result') != 'Ok':
            return []
        runs: List[Dict[str, Any]] = []
        for row in response.get('row', []):
            stats = row.get('measurement_stats') or {}
            elapsed_max = stats.get('elapsed_s_max')
            runs.append({
                'run_id': row.get('run_id'),
                'run_index': row.get('run_index'),
                'label': row.get('label'),
                'started_at': row.get('started_at'),
                'stopped_at': row.get('stopped_at') or '',
                'status': row.get('status'),
                'sample_count': int(stats.get('count', 0) or 0),
                'duration_s': float(elapsed_max) if elapsed_max is not None else None,
            })
        return runs

    def _steps_table(self, program_id: int) -> List[List[Any]]:
        return [[s.step_id, s.t_start, s.t_stop, s.minutes] for s in self._get_program_steps(program_id)]

    # --- UI handlers (FastAPI routes call these) ---
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

    def ui_save_im3536_config(
        self,
        interface: str,
        port: str,
        baudrate: float,
        host: str,
        lan_port: float,
        terminator: str,
        restart: bool,
    ) -> Tuple[str, str]:
        msg = self._save_env_section(
            {
                'DELATOMETRY_IM3536_INTERFACE': str(interface).strip().lower(),
                'DELATOMETRY_IM3536_PORT': str(port).strip(),
                'DELATOMETRY_IM3536_BAUDRATE': str(int(baudrate)),
                'DELATOMETRY_IM3536_HOST': str(host).strip(),
                'DELATOMETRY_IM3536_LAN_PORT': str(int(lan_port)),
                'DELATOMETRY_IM3536_TERMINATOR': str(terminator).strip().lower(),
            },
            'delatometry-im3536.service',
            bool(restart),
        )
        return msg, msg

    def ui_save_measure_source(self, source: str, restart: bool) -> str:
        from webui.measure_source import normalize_measure_source

        normalized = normalize_measure_source(source)
        ok, msg = write_env_file(
            self.delatometry_env_file,
            {'DELATOMETRY_MEASURE_SOURCE': normalized},
        )
        if not ok:
            return f'Failed to save measure source: {msg}'

        topics = resolve_measure_topics_from_env(read_env_file(self.delatometry_env_file))
        self.measure_source = topics['source']
        self.measure_topic = topics['measure_topic']
        self.measure_command_topic = topics['measure_command_topic']

        if not restart:
            return f'Saved measure source={normalized}. Restart core, webui, and meter services to apply.'

        active = 'delatometry-im3536.service' if normalized == 'im3536' else 'delatometry-measure-device.service'
        inactive = 'delatometry-measure-device.service' if normalized == 'im3536' else 'delatometry-im3536.service'
        systemd_ops.control_unit(inactive, 'stop', use_sudo=True)
        systemd_ops.control_unit(active, 'start', use_sudo=True)
        for svc in ('delatometry-core.service', 'delatometry-webui.service'):
            restart_service(svc)
        return f'Switched measure source to {normalized} ({topics["measure_topic"]}).'

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
        rows, err = self._programs_table(force_refresh=True)
        return rows, err or f'{len(rows)} program(s).'

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
                rows, _ = self._programs_table(force_refresh=True)
                return 0.0, rows, f'ERROR: {response.get("error", "unknown")}'
            program_id = int(response.get('ID', 0))
            self._log(f'Created program {program_id}')
            rows, _ = self._programs_table(force_refresh=True)
            return float(program_id), rows, f'Created program {program_id}.'
        except Exception as exc:
            return 0.0, [], f'ERROR: {exc}'

    def ui_duplicate_program(self, program_id: float) -> Tuple[float, List[List[Any]], str]:
        if not self._db_available():
            return 0.0, [], self._database_unavailable_message()
        source_id = int(program_id)
        if source_id <= 0:
            rows, _ = self._programs_table()
            return 0.0, rows, 'Select a program to duplicate.'
        try:
            created = self._db_query({'cmd': 'new_program'})
            if created.get('result') != 'Ok':
                rows, _ = self._programs_table()
                return 0.0, rows, 'Failed to create new program.'
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
            rows, _ = self._programs_table(force_refresh=True)
            return float(new_id), rows, f'Duplicated as program {new_id}.'
        except Exception as exc:
            return 0.0, [], f'ERROR: {exc}'

    def ui_delete_program(self, program_id: float) -> Tuple[List[List[Any]], str, float]:
        if not self._db_available():
            return [], self._database_unavailable_message(), program_id
        program_id_int = int(program_id)
        if program_id_int <= 0:
            rows, _ = self._programs_table()
            return rows, 'Invalid program ID.', program_id
        with self._lock:
            active_pid = self._core_program_status.get('program_id')
        if active_pid is not None and int(active_pid) == program_id_int:
            rows, _ = self._programs_table()
            return rows, 'Stop the running program first.', program_id
        try:
            response = self._db_query({'cmd': 'program_delete_by_id', 'id': program_id_int})
            if response.get('result') != 'Ok':
                rows, _ = self._programs_table()
                return rows, f'ERROR: {response.get("error", "delete failed")}', program_id
            self._log(f'Deleted program {program_id_int}')
            rows, _ = self._programs_table(force_refresh=True)
            return rows, f'Deleted program {program_id_int}.', 0.0
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
        if not self._core_available():
            return self._core_unavailable_message(), self._experiment_banner()
        program_id_int = int(program_id)
        try:
            response = self._core_query({
                'program': {'cmd': 'start', 'program_id': program_id_int},
            })
            if str(response.get('result', '')).lower() not in ('ok', 'true'):
                return f'ERROR: {response.get("error", "core rejected start")}', self._experiment_banner()
            prog = response.get('program') or {}
            with self._lock:
                self._core_program_status = dict(prog)
                self._program_was_running = True
            self._ensure_e720_sweep_for_running_program()
            self._sweep.reset()
            run_index = prog.get('run_index')
            label = prog.get('run_label') or f'{program_id_int}.{run_index or "?"}'
            self._log(f'Core started program {program_id_int} ({label})')
            return f'Program {program_id_int} started — experiment {label}.', self._experiment_banner()
        except Exception as exc:
            return f'ERROR: {exc}', self._experiment_banner()

    def ui_stop_program_by_id(self, program_id: float) -> Tuple[str, str]:
        if not self._core_available():
            return self._core_unavailable_message(), self._experiment_banner()
        program_id_int = int(program_id)
        try:
            with self._lock:
                run_id = self._core_program_status.get('run_id')
            response = self._core_query({
                'program': {'cmd': 'stop', 'program_id': program_id_int},
            })
            if str(response.get('result', '')).lower() not in ('ok', 'true'):
                return f'ERROR: {response.get("error", "stop failed")}', self._experiment_banner()
            self._sweep.reset()
            if run_id is not None:
                self._schedule_run_charts_on_finish(program_id_int, int(run_id))
            return f'Program {program_id_int} stopped.', self._experiment_banner()
        except Exception as exc:
            return f'ERROR: {exc}', self._experiment_banner()

    def get_experiment_status(self, *, refresh_core: bool = True) -> Dict[str, Any]:
        if refresh_core and self._core_available():
            try:
                response = self._core_query(
                    {'program': {'cmd': 'status'}},
                    timeout_sec=min(2.0, self.core_service_timeout_sec),
                )
            except Exception:
                pass
        self._ensure_e720_sweep_for_running_program()
        with self._lock:
            prog = dict(self._core_program_status)
            core = dict(self._last_core_snapshot.get('temperature_control') or {})
        timing = prog.get('timing') or IDLE_EXPERIMENT_TIMING
        mode = str(prog.get('mode') or 'idle')
        if mode == 'idle' and core.get('enabled'):
            mode = 'stabilize'
        program_id = prog.get('program_id')
        return {
            'mode': mode,
            'experiment_program_mode': normalize_experiment_mode(str(prog.get('experiment_mode', 'default'))),
            'banner': self._experiment_banner(),
            'program_id': program_id,
            'run_id': prog.get('run_id'),
            'run_index': prog.get('run_index'),
            'run_label': prog.get('run_label'),
            'program_status': 'Running' if program_id is not None else None,
            'timing': timing,
            'core': {
                'enabled': bool(core.get('enabled')),
                'target_k': core.get('target_k'),
                'reason': core.get('reason'),
                'heater_output': core.get('heater_output'),
            },
            'theoretical_temp_k': self._current_theoretical_temp_k(),
        }

    def ui_stop_program(self) -> Tuple[str, str]:
        if not self._core_available():
            return self._core_unavailable_message(), self._experiment_banner()
        try:
            with self._lock:
                program_id = self._core_program_status.get('program_id')
                run_id = self._core_program_status.get('run_id')
            if program_id is not None:
                msg, banner = self.ui_stop_program_by_id(float(program_id))
                if str(msg).startswith('ERROR'):
                    return msg, banner
                if run_id is not None:
                    self._schedule_run_charts_on_finish(int(program_id), int(run_id))
                return msg, banner
            response = self._core_query({'program': {'cmd': 'stop_all'}})
            if str(response.get('result', '')).lower() not in ('ok', 'true'):
                return f'ERROR: {response.get("error", "stop failed")}', self._experiment_banner()
            self._sweep.reset()
            return 'No active program. Control disabled.', self._experiment_banner()
        except Exception as exc:
            return f'ERROR: {exc}', self._experiment_banner()

    def _current_theoretical_temp_k(self) -> Optional[float]:
        """Program ramp target or manual/core setpoint for experiment chart agenda."""
        with self._lock:
            prog = self._core_program_status
            if prog.get('program_id') is not None and prog.get('last_target_k') is not None:
                return float(prog['last_target_k'])
            core = dict(self._last_core_snapshot.get('temperature_control') or {})
            if core.get('enabled'):
                return float(core.get('target_k', 0))
        return None

    def ui_manual_target(self, target_k: float, enabled: bool) -> str:
        if not self._core_available():
            return self._core_unavailable_message()
        if enabled:
            try:
                self._core_query({'program': {'cmd': 'stop_all'}})
            except Exception:
                pass
            self._sweep.reset()
        tc_payload: Dict[str, Any] = {
            'enabled': bool(enabled),
            'target_k': float(target_k),
        }
        if enabled:
            tc_payload['reset_integral'] = True
        try:
            response = self._core_query({'temperature_control': tc_payload})
            if str(response.get('result', '')).lower() in ('false', 'error'):
                return str(response.get('error', 'Core rejected temperature control'))
            scheduler_note = str(response.get('program_scheduler_note', '') or '')
            if enabled and scheduler_note:
                return f'ERROR: {scheduler_note}'
            tc = response.get('temperature_control')
            if enabled and not tc:
                return (
                    'Heater control did not start. Enable PWM in Configuration → Core '
                    '(PWM control + restart delatometry-core.service), and ensure LTM data is live.'
                )
            if enabled:
                self._log(f'Manual control ON, target {float(target_k):.2f} K')
                return f'Manual control started — target {float(target_k):.2f} K (PWM both channels).'
            self._log('Manual control OFF')
            return 'Manual control disabled.'
        except Exception as exc:
            return f'ERROR: {exc}'

    def ui_export_program(self, program_id: float, limit: float, clear_first: bool) -> Tuple[Optional[str], str]:
        if not self._db_available():
            return None, self._database_unavailable_message()
        program_id_int = int(program_id)
        if program_id_int <= 0:
            return None, 'Select a valid program ID.'
        try:
            if clear_first:
                self._db_query({'cmd': 'measurement_delete_by_program_id', 'program_id': program_id_int})
            result = export_program_archive(
                self._db_query,
                program_id_int,
                self.export_dir,
                limit=int(limit),
                charts_root=self.run_charts_dir,
            )
            if not result.get('ok'):
                return None, result.get('error', 'export failed')
            self._log(f'Exported program {program_id_int} -> {result["zip_path"]}')
            charts_note = f', {len(result.get("chart_files", []))} chart(s)' if result.get('chart_files') else ''
            trunc_note = ''
            if result.get('truncated_runs'):
                trunc_note = f' (truncated {len(result["truncated_runs"])} run(s) — see meta.json)'
            return result['zip_path'], (
                f'Export OK: {result["measurement_count"]} measurements in '
                f'{result.get("run_count", 0)} run file(s), {result["step_count"]} steps'
                f'{charts_note}{trunc_note}.'
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
            'im3536': snap['im3536'],
            'measure': snap['measure'],
            'db': snap['database'],
            'core': core,
            'ads': snap['ads1256'],
            'ltm_port_choices': serial_port_choices(snap['ltm2985']['port']),
            'meas_port_choices': serial_port_choices(snap['measure_device']['port']),
            'im3536_port_choices': serial_port_choices(snap['im3536']['port']),
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

    def _experiment_pwm_status(self) -> Dict[str, Any]:
        with self._lock:
            pwm = dict(self._last_core_snapshot.get('pwm') or {})
            tc = dict(self._last_core_snapshot.get('temperature_control') or {})
        core_cfg = get_configuration_snapshot(self.delatometry_env_file).get('core', {})
        pwm_enabled_cfg = bool(core_cfg.get('enable_pwm_controller'))
        core_ok = self._core_available()
        has_live_pwm = bool(pwm) or bool(tc)

        if not core_ok:
            return {'available': False, 'message': 'Core service unavailable'}
        if not pwm_enabled_cfg and not has_live_pwm:
            return {
                'available': False,
                'message': 'PWM disabled — enable in Configuration → Core and restart delatometry-core',
            }

        pwm_range = max(1, int(pwm.get('pwm_range') or 1000))
        pin_ch1 = int(pwm.get('pwm_pin') or core_cfg.get('pwm_pin_ch1') or 18)
        pin_ch2 = int(pwm.get('pwm_pin_ch2') or core_cfg.get('pwm_pin_ch2') or 19)
        duty_ch1 = int(pwm.get('heater_pwm', tc.get('heater_output', 0)) or 0)
        duty_ch2 = int(pwm.get('heater_pwm_ch2', duty_ch1) or 0)
        commanded = int(tc.get('heater_output', duty_ch1) or 0)

        def _channel(label: str, pin: int, duty: int) -> Dict[str, Any]:
            duty_clamped = max(0, min(duty, pwm_range))
            percent = round(100.0 * duty_clamped / pwm_range, 1)
            return {
                'label': label,
                'pin': pin,
                'duty': duty_clamped,
                'percent': percent,
                'pwm_range': pwm_range,
            }

        channels = [
            _channel('CH1', pin_ch1, duty_ch1),
            _channel('CH2', pin_ch2, duty_ch2),
        ]
        return {
            'available': True,
            'pwm_range': pwm_range,
            'control_enabled': bool(tc.get('enabled')),
            'control_reason': str(tc.get('reason') or ''),
            'commanded_output': commanded,
            'channels': channels,
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
        theoretical_temp = self._current_theoretical_temp_k()
        status = self.get_experiment_status(refresh_core=False)
        return {
            'banner': status['banner'],
            'experiment_mode': status['mode'],
            'experiment_program_mode': status.get('experiment_program_mode', 'default'),
            'run_label': status.get('run_label'),
            'timing': status['timing'],
            'measurements': self._measurements_table(),
            'ltm_summary': self._ltm_temperature_summary(),
            'ltm_stream': ltm_lines,
            'e720_summary': e720_summary_text(e720_data),
            'e720_row': e720_table_row(e720_data),
            'e720_stream': e720_lines,
            'core_json': core,
            'control_temp': control_temp,
            'theoretical_temp': theoretical_temp,
            'control_enabled': bool(core.get('enabled')),
            'heater_output': core.get('heater_output'),
            'pwm_status': self._experiment_pwm_status(),
        }

    def clear_new_program_draft(self) -> None:
        with self._lock:
            self._new_program_draft = []
            self._new_program_description = ''
            self._new_program_sweep_mode = 0
            self._new_program_experiment_mode = 'default'
            self._new_program_enabled_freqs = ['1000']
            self._new_program_range_max = 10000.0

    def get_new_program_draft_meta(self) -> Dict[str, Any]:
        with self._lock:
            return {
                'description': self._new_program_description,
                'experiment_mode': self._new_program_experiment_mode,
                'sweep_mode': self._new_program_sweep_mode,
                'enabled_freqs': list(self._new_program_enabled_freqs),
                'range_max': self._new_program_range_max,
            }

    def set_new_program_draft_meta(
        self,
        description: Optional[str] = None,
        experiment_mode: Optional[str] = None,
        sweep_mode: Optional[int] = None,
        enabled_freqs: Optional[List[str]] = None,
        range_max: Optional[float] = None,
    ) -> None:
        with self._lock:
            if description is not None:
                self._new_program_description = str(description).strip()
            if experiment_mode is not None:
                self._new_program_experiment_mode = normalize_experiment_mode(str(experiment_mode))
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
            experiment_mode=str(form.get('experiment_mode', 'default') or 'default'),
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
            experiment_mode = self._new_program_experiment_mode
        next_id = len(draft) + 1
        candidate = draft + [[next_id, float(t_start), float(t_stop), float(minutes)]]
        ok, issues = validate_temperature_steps(candidate, experiment_mode=experiment_mode)
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
        experiment_mode: str = 'default',
    ) -> str:
        with self._lock:
            draft = [list(row) for row in self._new_program_draft]
        check = validate_new_program(
            description,
            draft,
            int(mode),
            enabled_freqs,
            float(range_max),
            experiment_mode=experiment_mode,
        )
        if not check.can_create:
            return check.issues[0].message if check.issues else 'Cannot create program.'
        return self.ui_program_create_save_new_page(
            description,
            draft,
            float(mode),
            enabled_freqs,
            range_max,
            experiment_mode=experiment_mode,
        )

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
        rows, err = self._programs_table(force_refresh=True)
        return rows, err or f'{len(rows)} program(s) in database.'

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
            rows, _ = self._programs_table()
            return rows, 'Select a program first.'
        with self._lock:
            active_pid = self._core_program_status.get('program_id')
        if active_pid is not None and int(active_pid) == pid:
            rows, _ = self._programs_table()
            return rows, 'Stop the running program before deleting it.'
        try:
            self._db_query({'cmd': 'measurement_delete_by_program_id', 'program_id': pid})
            response = self._db_query({'cmd': 'program_delete_by_id', 'id': pid})
            if response.get('result') != 'Ok':
                msg = f'Delete failed: {response.get("error", "unknown")}'
            else:
                msg = f'Program {pid} and its data were deleted.'
                self._log(msg)
            rows, err = self._programs_table(force_refresh=True)
            return rows, err or msg
        except Exception as exc:
            return [], f'ERROR: {exc}'

    def ui_programs_action_export(self, program_id: Any) -> Tuple[Optional[str], str]:
        pid = self._parse_program_id(program_id)
        if not self._db_available():
            return None, self._database_unavailable_message()
        if pid <= 0:
            return None, 'Select a program to export.'
        try:
            result = export_program_archive(
                self._db_query,
                pid,
                self.export_dir,
                limit=50000,
                charts_root=self.run_charts_dir,
            )
            if not result.get('ok'):
                return None, result.get('error', 'export failed')
            self._log(f'Exported program {pid} -> {result["zip_path"]}')
            charts_note = f', {len(result.get("chart_files", []))} chart(s)' if result.get('chart_files') else ''
            return result['zip_path'], (
                f'Export ready: {result["measurement_count"]} measurements in '
                f'{result.get("run_count", 0)} run file(s), {result["step_count"]} steps{charts_note}.'
            )
        except Exception as exc:
            return None, f'ERROR: {exc}'

    def ui_export_program_run(self, program_id: int, run_id: int) -> Tuple[Optional[str], str]:
        pid = int(program_id or 0)
        rid = int(run_id or 0)
        if not self._db_available():
            return None, self._database_unavailable_message()
        if pid <= 0 or rid <= 0:
            return None, 'Invalid program or run.'
        try:
            result = export_run_archive(
                self._db_query,
                pid,
                rid,
                self.export_dir,
                limit=50000,
                charts_root=self.run_charts_dir,
            )
            if not result.get('ok'):
                return None, result.get('error', 'export failed')
            label = result.get('run_label', f'{pid}.{rid}')
            self._log(f'Exported run {label} -> {result["zip_path"]}')
            charts_note = f', {len(result.get("chart_files", []))} chart(s)' if result.get('chart_files') else ''
            trunc_note = ''
            if result.get('truncated_runs'):
                trunc_note = ' (row limit applied — see meta.json)'
            return result['zip_path'], (
                f'Export ready: run {label}, {result["measurement_count"]} measurement(s)'
                f'{charts_note}{trunc_note}.'
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
                'experiment_mode': 'default',
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
            meta = row.get('meta') if isinstance(row.get('meta'), dict) else {}
            return {
                'header': f"Program {row['id']} — {row['datetime']} — status: {row['status']}",
                'description': str(row.get('description') or ''),
                'status': str(row.get('status') or ''),
                'steps': steps,
                'experiment_mode': normalize_experiment_mode(str(meta.get('experiment_mode', 'default'))),
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
                'experiment_mode': 'default',
                'enabled_freqs': ['1000'],
                'range_max': 10000.0,
                'run_status': f'ERROR: {exc}',
            }

    def _sync_core_program_status(self) -> None:
        if not self._core_available():
            return
        try:
            response = self._core_query({'program': {'cmd': 'status'}})
            if str(response.get('result', '')).lower() in ('ok', 'true'):
                with self._lock:
                    self._core_program_status = dict(response.get('program') or {})
        except Exception:
            pass

    def program_view_fields(self, program_id: int) -> Dict[str, Any]:
        pid = int(program_id or 0)
        self._sync_core_program_status()
        if not self._db_available() or pid <= 0:
            return {
                'summary': 'Select a program on the list first.',
                'steps': [],
                'e720_json': '{}',
                'program_runs': [],
                'message': 'Select a program first.',
                'db_status': '',
                'is_running_here': False,
                'can_run': False,
                'can_stop': False,
            }
        try:
            detail = self._db_query({'cmd': 'get_program_detail', 'id': pid})
            if detail.get('result') != 'Ok':
                return {
                    'summary': f'Program {pid}',
                    'steps': [],
                    'e720_json': '{}',
                    'program_runs': [],
                    'message': detail.get('error', 'not found'),
                    'db_status': '',
                    'is_running_here': False,
                    'can_run': False,
                    'can_stop': False,
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
            db_status = str(row.get('status') or 'New')
            program_runs = self._program_runs_table(pid)
            with self._lock:
                prog = self._core_program_status
                active_id = prog.get('program_id')
                active_run_id = prog.get('run_id')
            is_running_here = active_id is not None and int(active_id) == pid
            for run in program_runs:
                run['is_active'] = (
                    is_running_here
                    and active_run_id is not None
                    and int(run.get('run_id', 0)) == int(active_run_id)
                )
            return {
                'summary': summary,
                'steps': steps,
                'e720_json': json.dumps(row.get('e720') or {}, indent=2, default=str),
                'program_runs': program_runs,
                'message': f'Loaded program {pid}.',
                'db_status': db_status,
                'is_running_here': is_running_here,
                'can_run': len(steps) > 0 and not is_running_here,
                'can_stop': is_running_here or db_status.lower() == 'running',
            }
        except Exception as exc:
            return {
                'summary': f'Program {pid}',
                'steps': [],
                'e720_json': '{}',
                'program_runs': [],
                'message': f'ERROR: {exc}',
                'db_status': '',
                'is_running_here': False,
                'can_run': False,
                'can_stop': False,
            }

    def ui_program_create_save_new_page(
        self,
        description: str,
        draft_steps: Any,
        mode: float,
        enabled_freqs: List[str],
        range_max: float,
        experiment_mode: str = 'default',
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
            self._db_query({
                'cmd': 'set_program_meta',
                'program_id': program_id,
                'key': 'experiment_mode',
                'value': normalize_experiment_mode(experiment_mode),
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
            self._refresh_programs_cache()
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
        experiment_mode: str = 'default',
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
            mode_resp = self._db_query({
                'cmd': 'set_program_meta',
                'program_id': program_id,
                'key': 'experiment_mode',
                'value': normalize_experiment_mode(experiment_mode),
            })
            if mode_resp.get('result') != 'Ok':
                return f'ERROR: {mode_resp.get("error", "experiment mode save failed")}'
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

    def launch_web(self) -> None:
        import uvicorn

        from webui.web_app import create_app

        app = create_app(self)
        self._log(
            f'Web UI at http://{self.bind_host}:{self.bind_port} '
            f'(auth_enabled={self.auth_enabled}, live: /ws/dashboard, /api/dashboard/snapshot)'
        )
        uvicorn.run(app, host=self.bind_host, port=self.bind_port, log_level='info')


def _executor_spin(executor: MultiThreadedExecutor, node: WebHMINode) -> None:
    node._ros_executor_thread_id = threading.get_ident()
    executor.spin()


def main(args: Optional[List[str]] = None) -> None:
    rclpy.init(args=args)
    node = WebHMINode()
    executor = MultiThreadedExecutor(num_threads=6)
    executor.add_node(node)
    thread = threading.Thread(target=_executor_spin, args=(executor, node), daemon=True)
    thread.start()
    # Allow executor thread to start before uvicorn issues service calls from another thread.
    for _ in range(50):
        if node._ros_executor_thread_id is not None:
            break
        time.sleep(0.02)
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
