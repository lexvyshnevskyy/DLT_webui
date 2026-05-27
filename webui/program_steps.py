from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Mapping

from webui.dataframe_utils import dataframe_to_rows

_STEP_FIELD_RE = re.compile(r'^step_(\d+)_(t_start|t_stop|minutes)$')


def parse_step_field_updates(form: Mapping[str, Any]) -> Dict[int, Dict[str, float]]:
    """Extract per-step edits from form keys like step_12_t_start."""
    updates: Dict[int, Dict[str, float]] = {}
    for key, raw in form.items():
        match = _STEP_FIELD_RE.match(str(key))
        if not match:
            continue
        step_id = int(match.group(1))
        field = match.group(2)
        updates.setdefault(step_id, {})[field] = float(raw)
    return updates

STEP_TABLE_HEADERS = ['step_id', 't_start_k', 't_stop_k', 'minutes', '🗑']
STEP_TABLE_DATATYPES = ['number', 'number', 'number', 'number', 'str']
STEP_STATIC_COLUMNS = [0, 4]  # step_id and delete column are not editable
DEFAULT_NEW_STEP = (40.0, 100.0, 15.0)
DELETE_MARKERS = frozenset({'delete', 'del', 'x', 'remove', 'yes'})
HEADER_MARKERS = frozenset({'step_id', 'id', 't_start_k', 't_stop_k', 'minutes'})


def _cell_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return str(value).strip() == ''


def _is_header_row(row: List[Any]) -> bool:
    if not row:
        return True
    first = str(row[0]).strip().lower()
    return first in HEADER_MARKERS


def steps_to_pandas(rows: List[List[Any]]) -> Any:
    """Build pandas DataFrame for gr.Dataframe (shows all rows + proper column headers)."""
    import pandas as pd

    data: List[List[Any]] = []
    for row in rows:
        if len(row) < 4 or _is_header_row(list(row)):
            continue
        marker = str(row[4] or '🗑') if len(row) > 4 else '🗑'
        data.append([int(row[0]), float(row[1]), float(row[2]), float(row[3]), marker or '🗑'])
    if not data:
        return pd.DataFrame(columns=STEP_TABLE_HEADERS)
    return pd.DataFrame(data, columns=STEP_TABLE_HEADERS)


def normalize_step_rows(table: Any, mark_delete_col: bool = True) -> List[List[Any]]:
    """Parse table, drop empty/delete/header rows, renumber step_id."""
    rows_out: List[List[Any]] = []
    for row in dataframe_to_rows(table):
        row_list = list(row)
        if len(row_list) < 4 or _is_header_row(row_list):
            continue
        if mark_delete_col and len(row_list) > 4:
            if str(row_list[4] or '').strip().lower() in DELETE_MARKERS:
                continue
        if _cell_blank(row_list[1]) or _cell_blank(row_list[2]) or _cell_blank(row_list[3]):
            continue
        try:
            t_start = float(row_list[1])
            t_stop = float(row_list[2])
            minutes = float(row_list[3])
        except (TypeError, ValueError):
            continue
        rows_out.append([0, t_start, t_stop, minutes, '🗑'])
    for idx, row in enumerate(rows_out):
        row[0] = idx + 1
    return rows_out


def program_id_from_request(request: Any) -> int:
    if request is None or not getattr(request, 'query_params', None):
        return 0
    raw = request.query_params.get('id', '0')
    if isinstance(raw, list):
        raw = raw[0] if raw else '0'
    try:
        return int(float(str(raw or '0')))
    except (TypeError, ValueError):
        return 0
