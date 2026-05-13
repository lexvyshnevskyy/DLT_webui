from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional, Tuple

import gradio as gr
import rclpy
from database.srv import Query as DatabaseQuery
CoreQuery = DatabaseQuery
from msgs.msg import Measurement
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node


@dataclass
class ProgramStep:
    step_id: int
    t_start: float
    t_stop: float
    minutes: float


class WebHMINode(Node):
    def __init__(self) -> None:
        super().__init__('webui')

        self.declare_parameter('core_service', '/core/query')
        self.declare_parameter('database_service', '/database/query')
        self.declare_parameter('measurement_topic', '/ltm2985/measurement')
        self.declare_parameter('bind_host', '0.0.0.0')
        self.declare_parameter('bind_port', 7860)
        self.declare_parameter('title', 'Experiment Control')
        self.declare_parameter('queue_enabled', False)
        self.declare_parameter('auth_enabled', False)
        self.declare_parameter('auth_user', 'admin')
        self.declare_parameter('auth_password', 'admin')
        self.declare_parameter('status_refresh_period_sec', 1.0)
        self.declare_parameter('control_loop_period_sec', 1.0)

        self.core_service = str(self.get_parameter('core_service').value)
        self.database_service = str(self.get_parameter('database_service').value)
        self.measurement_topic = str(self.get_parameter('measurement_topic').value)
        self.bind_host = str(self.get_parameter('bind_host').value)
        self.bind_port = int(self.get_parameter('bind_port').value)
        self.title = str(self.get_parameter('title').value)
        self.queue_enabled = bool(self.get_parameter('queue_enabled').value)
        self.auth_enabled = bool(self.get_parameter('auth_enabled').value)
        self.auth_user = str(self.get_parameter('auth_user').value)
        self.auth_password = str(self.get_parameter('auth_password').value)
        self.status_refresh_period_sec = float(self.get_parameter('status_refresh_period_sec').value)
        self.control_loop_period_sec = float(self.get_parameter('control_loop_period_sec').value)

        self.core_client = self.create_client(CoreQuery, self.core_service)
        self.db_client = self.create_client(DatabaseQuery, self.database_service)
        self.create_subscription(Measurement, self.measurement_topic, self._on_measurement, 100)

        self._lock = threading.RLock()
        self._latest_measurements: Dict[int, Dict[str, Any]] = {}
        self._active_program_id: Optional[int] = None
        self._active_program_steps: List[ProgramStep] = []
        self._active_step_index: int = 0
        self._active_step_started_monotonic: Optional[float] = None
        self._last_target_k: Optional[float] = None
        self._last_core_snapshot: Dict[str, Any] = {}
        self._last_db_program_status: str = 'Idle'
        self._log_lines: Deque[str] = deque(maxlen=200)

        self._control_timer = self.create_timer(self.control_loop_period_sec, self._control_tick)

        self._log('webui node started')
        self.get_logger().info(
            f'webui ready. core_service={self.core_service}, '
            f'database_service={self.database_service}, measurement_topic={self.measurement_topic}'
        )

    def _log(self, message: str) -> None:
        stamp = time.strftime('%Y-%m-%d %H:%M:%S')
        line = f'[{stamp}] {message}'
        with self._lock:
            self._log_lines.appendleft(line)
        self.get_logger().info(message)

    def _on_measurement(self, msg: Measurement) -> None:
        payload = {
            'channel': int(msg.channel),
            'type': str(msg.type),
            'value': float(msg.value),
            'valid': bool(msg.valid),
            'updated_monotonic': time.monotonic(),
        }
        with self._lock:
            self._latest_measurements[int(msg.channel)] = payload

    def _call_service_json(self, client: Any, request_type: Any, service_name: str, payload: Dict[str, Any], timeout_sec: float = 5.0) -> Dict[str, Any]:
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
                raw = response.response or '{}'
                return json.loads(raw)
            time.sleep(0.02)
        raise TimeoutError(f'Timeout waiting for {service_name}')

    def _db_query(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._call_service_json(self.db_client, DatabaseQuery.Request, self.database_service, payload)

    def _core_query(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = self._call_service_json(self.core_client, CoreQuery.Request, self.core_service, payload)
        with self._lock:
            self._last_core_snapshot = result
        return result

    def _get_program_steps(self, program_id: int) -> List[ProgramStep]:
        response = self._db_query({'cmd': 'program_step_list', 'id': program_id})
        if response.get('result') != 'Ok':
            raise RuntimeError(response.get('error', 'Failed to get program steps'))

        steps: List[ProgramStep] = []
        for raw_row in response.get('row', []):
            parts = str(raw_row).split('^')
            if len(parts) < 4:
                continue
            steps.append(
                ProgramStep(
                    step_id=int(parts[0]),
                    t_start=float(parts[1]),
                    t_stop=float(parts[2]),
                    minutes=float(parts[3]),
                )
            )
        steps.sort(key=lambda item: item.step_id)
        return steps

    @staticmethod
    def _interpolate_target(step: ProgramStep, elapsed_s: float) -> float:
        duration_s = max(0.0, float(step.minutes) * 60.0)
        if duration_s <= 0.0:
            return float(step.t_stop)
        alpha = min(max(elapsed_s / duration_s, 0.0), 1.0)
        return float(step.t_start) + (float(step.t_stop) - float(step.t_start)) * alpha

    def _control_tick(self) -> None:
        try:
            with self._lock:
                program_id = self._active_program_id
                if program_id is None:
                    return
                steps = list(self._active_program_steps)
                step_index = self._active_step_index
                step_started = self._active_step_started_monotonic

            if step_index >= len(steps):
                self._finish_active_program('Finished')
                return

            step = steps[step_index]
            if step_started is None:
                with self._lock:
                    self._active_step_started_monotonic = time.monotonic()
                step_started = self._active_step_started_monotonic
                self._log(f'Program {program_id}: starting step {step_index + 1}/{len(steps)} (id={step.step_id})')

            elapsed_s = max(0.0, time.monotonic() - float(step_started))
            duration_s = max(0.0, float(step.minutes) * 60.0)
            target_k = self._interpolate_target(step, elapsed_s)

            self._core_query(
                {
                    'temperature_control': {
                        'enabled': True,
                        'target_k': target_k,
                    }
                }
            )
            with self._lock:
                self._last_target_k = target_k
                self._last_db_program_status = f'Running step {step_index + 1}/{len(steps)}'

            if elapsed_s >= duration_s:
                with self._lock:
                    self._active_step_index += 1
                    self._active_step_started_monotonic = time.monotonic()
                    finished = self._active_step_index >= len(self._active_program_steps)
                if finished:
                    self._finish_active_program('Finished')
                else:
                    self._log(f'Program {program_id}: advancing to step {self._active_step_index + 1}/{len(steps)}')
        except Exception as exc:
            self._log(f'Control loop error: {exc}')

    def _finish_active_program(self, final_status: str) -> None:
        with self._lock:
            program_id = self._active_program_id
            if program_id is None:
                return
        try:
            self._core_query({'temperature_control': {'enabled': False}})
        except Exception as exc:
            self._log(f'Failed to disable control for program {program_id}: {exc}')
        try:
            self._db_query({'cmd': 'program_update_status', 'id': program_id, 'status': final_status})
        except Exception as exc:
            self._log(f'Failed to update DB status for program {program_id}: {exc}')
        with self._lock:
            self._active_program_id = None
            self._active_program_steps = []
            self._active_step_index = 0
            self._active_step_started_monotonic = None
            self._last_db_program_status = final_status
        self._log(f'Program {program_id} ended with status={final_status}')

    def _current_status_dict(self) -> Dict[str, Any]:
        with self._lock:
            measurements = dict(self._latest_measurements)
            active_program_id = self._active_program_id
            step_index = self._active_step_index
            step_count = len(self._active_program_steps)
            last_target_k = self._last_target_k
            last_db_program_status = self._last_db_program_status
            core_snapshot = dict(self._last_core_snapshot)
        return {
            'active_program_id': active_program_id,
            'program_status': last_db_program_status,
            'active_step_index': step_index + 1 if active_program_id is not None and step_count > 0 else 0,
            'step_count': step_count,
            'last_target_k': last_target_k,
            'measurements': measurements,
            'core': core_snapshot,
        }

    def _measurements_table(self) -> List[List[Any]]:
        now = time.monotonic()
        rows: List[List[Any]] = []
        with self._lock:
            items = sorted(self._latest_measurements.items())
        for channel, item in items:
            age_s = max(0.0, now - float(item['updated_monotonic']))
            rows.append([
                channel,
                item.get('type'),
                item.get('value'),
                item.get('valid'),
                round(age_s, 3),
            ])
        return rows

    def _programs_table(self) -> List[List[Any]]:
        response = self._db_query({'cmd': 'program_all_list'})
        if response.get('result') != 'Ok':
            raise RuntimeError(response.get('error', 'Failed to get programs'))
        rows: List[List[Any]] = []
        for raw_row in response.get('row', []):
            parts = str(raw_row).split('^')
            if len(parts) < 3:
                continue
            rows.append([int(parts[0]), parts[1], parts[2]])
        return rows

    def _steps_table(self, program_id: int) -> List[List[Any]]:
        rows: List[List[Any]] = []
        for step in self._get_program_steps(program_id):
            rows.append([step.step_id, step.t_start, step.t_stop, step.minutes])
        return rows

    def ui_refresh_status(self) -> Tuple[str, List[List[Any]], str]:
        status = self._current_status_dict()
        summary = {
            'active_program_id': status['active_program_id'],
            'program_status': status['program_status'],
            'active_step_index': status['active_step_index'],
            'step_count': status['step_count'],
            'last_target_k': status['last_target_k'],
            'temperature_control': status.get('core', {}).get('temperature_control'),
        }
        log_text = '\n'.join(list(self._log_lines))
        return json.dumps(summary, indent=2), self._measurements_table(), log_text

    def ui_refresh_programs(self) -> Tuple[List[List[Any]], str]:
        rows = self._programs_table()
        return rows, f'Loaded {len(rows)} program(s).'

    def ui_load_program(self, program_id: float) -> Tuple[List[List[Any]], str]:
        program_id_int = int(program_id)
        rows = self._steps_table(program_id_int)
        return rows, f'Loaded {len(rows)} step(s) for program {program_id_int}.'

    def ui_create_program(self) -> Tuple[float, List[List[Any]], str]:
        response = self._db_query({'cmd': 'new_program'})
        if response.get('result') != 'Ok':
            raise RuntimeError(response.get('error', 'Failed to create program'))
        program_id = int(response.get('ID', 0))
        self._log(f'Created program {program_id}')
        return float(program_id), self._programs_table(), f'Created program {program_id}.'

    def ui_add_step(self, program_id: float, t_start: float, t_stop: float, minutes: float) -> Tuple[List[List[Any]], str]:
        program_id_int = int(program_id)
        response = self._db_query(
            {
                'cmd': 'program_step_insert',
                'program_id': program_id_int,
                't_start': float(t_start),
                't_stop': float(t_stop),
                'minutes': float(minutes),
            }
        )
        if response.get('result') != 'Ok':
            raise RuntimeError(response.get('error', 'Failed to add step'))
        self._log(f'Program {program_id_int}: added step t_start={t_start}, t_stop={t_stop}, minutes={minutes}')
        return self._steps_table(program_id_int), f'Added step to program {program_id_int}.'

    def ui_delete_step(self, program_id: float, step_id: float) -> Tuple[List[List[Any]], str]:
        program_id_int = int(program_id)
        response = self._db_query({'cmd': 'program_delete_temp', 'id': int(step_id)})
        if response.get('result') != 'Ok':
            raise RuntimeError(response.get('error', 'Failed to delete step'))
        self._log(f'Program {program_id_int}: deleted step {int(step_id)}')
        return self._steps_table(program_id_int), f'Deleted step {int(step_id)}.'

    def ui_start_program(self, program_id: float) -> str:
        program_id_int = int(program_id)
        steps = self._get_program_steps(program_id_int)
        if not steps:
            raise RuntimeError(f'Program {program_id_int} has no steps.')

        with self._lock:
            already_running = self._active_program_id is not None
        if already_running:
            raise RuntimeError('Another program is already running. Stop it first.')

        first_target_k = float(steps[0].t_start)
        self._core_query(
            {
                'temperature_control': {
                    'enabled': True,
                    'target_k': first_target_k,
                    'reset_integral': True,
                }
            }
        )
        self._db_query({'cmd': 'program_update_status', 'id': program_id_int, 'status': 'Running'})
        with self._lock:
            self._active_program_id = program_id_int
            self._active_program_steps = steps
            self._active_step_index = 0
            self._active_step_started_monotonic = None
            self._last_db_program_status = 'Running'
            self._last_target_k = first_target_k
        self._log(f'Program {program_id_int} started with {len(steps)} step(s).')
        return f'Program {program_id_int} started.'

    def ui_stop_program(self) -> str:
        with self._lock:
            program_id = self._active_program_id
        if program_id is None:
            self._core_query({'temperature_control': {'enabled': False}})
            with self._lock:
                self._last_db_program_status = 'Stopped'
            return 'No active program. Temperature control disabled.'
        self._finish_active_program('Stopped')
        return f'Program {program_id} stopped.'

    def ui_manual_target(self, target_k: float, enabled: bool) -> str:
        response = self._core_query({'temperature_control': {'enabled': bool(enabled), 'target_k': float(target_k)}})
        snapshot = response.get('temperature_control')
        self._log(f'Manual target update: enabled={enabled}, target_k={target_k}')
        return json.dumps(snapshot, indent=2)

    def build_ui(self) -> gr.Blocks:
        with gr.Blocks(title=self.title) as demo:
            gr.Markdown(f'# {self.title}')
            with gr.Tab('Status'):
                status_box = gr.Code(label='Experiment status', language='json')
                measurement_table = gr.Dataframe(
                    headers=['channel', 'type', 'value', 'valid', 'age_s'],
                    datatype=['number', 'str', 'number', 'bool', 'number'],
                    interactive=False,
                    label='Latest measurements',
                )
                logs_box = gr.Textbox(label='Logs', lines=12, interactive=False)
                refresh_status_btn = gr.Button('Refresh status')

            with gr.Tab('Programs'):
                program_id_box = gr.Number(label='Program ID', precision=0, value=0)
                programs_table = gr.Dataframe(
                    headers=['id', 'datetime', 'status'],
                    datatype=['number', 'str', 'str'],
                    interactive=False,
                    label='Programs',
                )
                programs_message = gr.Textbox(label='Programs message', interactive=False)
                with gr.Row():
                    create_program_btn = gr.Button('Create program')
                    refresh_programs_btn = gr.Button('Refresh programs')
                    load_program_btn = gr.Button('Load selected program')
                steps_table = gr.Dataframe(
                    headers=['step_id', 't_start_k', 't_stop_k', 'minutes'],
                    datatype=['number', 'number', 'number', 'number'],
                    interactive=False,
                    label='Program steps',
                )
                steps_message = gr.Textbox(label='Steps message', interactive=False)
                with gr.Row():
                    t_start_box = gr.Number(label='T start [K]', value=300.0)
                    t_stop_box = gr.Number(label='T stop [K]', value=350.0)
                    minutes_box = gr.Number(label='Minutes', value=10.0)
                with gr.Row():
                    add_step_btn = gr.Button('Add step')
                    delete_step_id_box = gr.Number(label='Delete step ID', precision=0, value=0)
                    delete_step_btn = gr.Button('Delete step')

            with gr.Tab('Control'):
                start_btn = gr.Button('Start selected program', variant='primary')
                stop_btn = gr.Button('Stop active program', variant='stop')
                control_message = gr.Textbox(label='Control message', interactive=False)
                gr.Markdown('### Manual control')
                with gr.Row():
                    manual_enabled_box = gr.Checkbox(label='Enable control', value=False)
                    manual_target_box = gr.Number(label='Target [K]', value=350.0)
                manual_apply_btn = gr.Button('Apply manual target')
                manual_snapshot_box = gr.Code(label='Core control snapshot', language='json')

            refresh_status_btn.click(self.ui_refresh_status, outputs=[status_box, measurement_table, logs_box])
            refresh_programs_btn.click(self.ui_refresh_programs, outputs=[programs_table, programs_message])
            create_program_btn.click(self.ui_create_program, outputs=[program_id_box, programs_table, programs_message])
            load_program_btn.click(self.ui_load_program, inputs=[program_id_box], outputs=[steps_table, steps_message])
            add_step_btn.click(
                self.ui_add_step,
                inputs=[program_id_box, t_start_box, t_stop_box, minutes_box],
                outputs=[steps_table, steps_message],
            )
            delete_step_btn.click(
                self.ui_delete_step,
                inputs=[program_id_box, delete_step_id_box],
                outputs=[steps_table, steps_message],
            )
            start_btn.click(self.ui_start_program, inputs=[program_id_box], outputs=[control_message])
            stop_btn.click(self.ui_stop_program, outputs=[control_message])
            manual_apply_btn.click(
                self.ui_manual_target,
                inputs=[manual_target_box, manual_enabled_box],
                outputs=[manual_snapshot_box],
            )

            demo.load(self.ui_refresh_status, outputs=[status_box, measurement_table, logs_box])
            demo.load(self.ui_refresh_programs, outputs=[programs_table, programs_message])
            timer = gr.Timer(value=self.status_refresh_period_sec)
            timer.tick(self.ui_refresh_status, outputs=[status_box, measurement_table, logs_box])

        return demo

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
        self._log(f'Launching Gradio UI on http://{self.bind_host}:{self.bind_port}')
        demo.launch(**launch_kwargs)


def main(args: Optional[List[str]] = None) -> None:
    rclpy.init(args=args)
    node = WebHMINode()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)

    executor_thread = threading.Thread(target=executor.spin, daemon=True)
    executor_thread.start()

    try:
        node.launch_ui()
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()
        executor_thread.join(timeout=2.0)


if __name__ == '__main__':
    main()
