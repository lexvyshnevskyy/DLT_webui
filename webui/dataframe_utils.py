from __future__ import annotations

from typing import Any, List


def dataframe_to_rows(table: Any) -> List[List[Any]]:
    """Convert Gradio Dataframe / pandas input to a plain list of rows."""
    if table is None:
        return []

    if hasattr(table, 'values'):
        try:
            return table.values.tolist()
        except Exception:
            pass

    if isinstance(table, dict):
        data = table.get('data')
        if data is not None:
            return [list(row) for row in data]
        values = table.get('values')
        if values is not None:
            return [list(row) for row in values]

    try:
        return [list(row) for row in list(table)]
    except TypeError:
        return []


def parse_temperature_steps(table: Any) -> List[dict]:
    """Parse step rows from draft/create table (includes optional delete column)."""
    from webui.program_steps import normalize_step_rows

    steps: List[dict] = []
    for row in normalize_step_rows(table, mark_delete_col=True):
        steps.append({'t_start': float(row[1]), 't_stop': float(row[2]), 'minutes': float(row[3])})
    return steps
