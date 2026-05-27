from __future__ import annotations

from typing import Any, Callable, Dict, Optional

DbQueryFn = Callable[[Dict[str, Any]], Dict[str, Any]]


def build_measurement_row(
    program_id: int,
    e720: Dict[str, Any],
    temperatures: Dict[int, Dict[str, Any]],
    control_channel: int,
    monitor_channel: int,
    target_k: Optional[float],
    *,
    run_id: Optional[int] = None,
    e720_offline: bool = False,
) -> Dict[str, Any]:
    control = temperatures.get(control_channel, {})
    monitor = temperatures.get(monitor_channel, {})
    if e720_offline:
        freq = 0.0
        measure_ch1 = 0.0
        measure_ch2 = 0.0
    else:
        freq = float(e720.get('frequency', 0.0) or 0.0)
        measure_ch1 = float(e720.get('firstvalue', 0.0) or 0.0)
        measure_ch2 = float(e720.get('secondvalue', 0.0) or 0.0)
    row: Dict[str, Any] = {
        'program_id': program_id,
        'freq': freq,
        'measure_ch1': measure_ch1,
        'measure_ch2': measure_ch2,
        't_ch1': float(control.get('value', 0.0) or 0.0),
        't_ch2': float(monitor.get('value', 0.0) or 0.0),
        't_exp': float(target_k if target_k is not None else 0.0),
    }
    if run_id is not None and int(run_id) > 0:
        row['run_id'] = int(run_id)
    return row


def insert_measurement(db_query: DbQueryFn, row: Dict[str, Any]) -> bool:
    response = db_query({'cmd': 'measurement_insert', **row})
    return str(response.get('result', '')).lower() == 'ok' and int(response.get('ID', 0) or 0) > 0
