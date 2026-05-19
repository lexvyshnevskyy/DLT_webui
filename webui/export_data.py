from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List

DbQueryFn = Callable[[Dict[str, Any]], Dict[str, Any]]


def _write_csv(path: Path, headers: List[str], rows: List[List[Any]]) -> None:
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def export_program_archive(
    db_query: DbQueryFn,
    program_id: int,
    export_dir: str,
    limit: int = 100000,
) -> Dict[str, Any]:
    import zipfile

    export_root = Path(export_dir)
    export_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    work_dir = export_root / f'program_{program_id}_{stamp}'
    work_dir.mkdir(parents=True, exist_ok=True)

    program_info = db_query({'cmd': 'get_program_by_id', 'id': program_id})
    steps_resp = db_query({'cmd': 'program_step_list', 'id': program_id})
    measurements_resp = db_query({'cmd': 'measurement_list', 'program_id': program_id, 'limit': limit})
    stats_resp = db_query({'cmd': 'measurement_stats', 'program_id': program_id})
    e720_resp = db_query({'cmd': 'get_e720', 'id': program_id})

    meta = {
        'program_id': program_id,
        'exported_at': datetime.now().isoformat(),
        'program': program_info,
        'e720_config': e720_resp.get('row', {}),
        'measurement_stats': stats_resp.get('row', stats_resp),
    }
    (work_dir / 'meta.json').write_text(json.dumps(meta, indent=2, default=str), encoding='utf-8')

    if program_info.get('result') == 'Ok' and program_info.get('row'):
        parts = str(program_info['row']).split('^')
        if len(parts) >= 3:
            _write_csv(
                work_dir / 'program.csv',
                ['id', 'datetime', 'status'],
                [[parts[0], parts[1], parts[2]]],
            )

    step_rows: List[List[Any]] = []
    for raw_row in steps_resp.get('row', []):
        parts = str(raw_row).split('^')
        if len(parts) >= 4:
            step_rows.append(parts[:4])
    _write_csv(work_dir / 'program_steps.csv', ['step_id', 't_start_k', 't_stop_k', 'minutes'], step_rows)

    measurement_rows: List[List[Any]] = []
    for row in measurements_resp.get('row', []):
        if isinstance(row, dict):
            measurement_rows.append([
                row.get('id', ''),
                row.get('elapsed_s', ''),
                row.get('freq', ''),
                row.get('measure_ch1', ''),
                row.get('measure_ch2', ''),
                row.get('t_ch1', ''),
                row.get('t_ch2', ''),
                row.get('t_exp', ''),
                row.get('created_at', ''),
            ])
    _write_csv(
        work_dir / 'measurements.csv',
        ['id', 'elapsed_s', 'freq', 'measure_ch1', 'measure_ch2', 't_ch1', 't_ch2', 't_exp', 'created_at'],
        measurement_rows,
    )

    zip_path = export_root / f'program_{program_id}_{stamp}.zip'
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in work_dir.iterdir():
            archive.write(file_path, arcname=file_path.name)

    return {
        'ok': True,
        'zip_path': str(zip_path),
        'measurement_count': len(measurement_rows),
        'step_count': len(step_rows),
        'error': '',
    }
