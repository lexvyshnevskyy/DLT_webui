from __future__ import annotations

import csv
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from webui.run_charts import _load_run_measurements, run_chart_dir

DbQueryFn = Callable[[Dict[str, Any]], Dict[str, Any]]

MEASUREMENT_HEADERS = [
    'id',
    'program_id',
    'run_id',
    'elapsed_s',
    'freq',
    'measure_ch1',
    'measure_ch2',
    't_ch1',
    't_ch2',
    't_exp',
    'created_at',
]


def _write_csv(path: Path, headers: List[str], rows: List[List[Any]]) -> None:
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def _label_slug(label: str, *, program_id: int, run_index: int, run_id: int) -> str:
    text = str(label or '').strip() or f'{program_id}.{run_index}'
    slug = re.sub(r'[^\w.\-]+', '_', text)
    base = slug or f'{program_id}.{run_index}'
    return f'{base}_run{int(run_id)}'


def _measurement_rows_to_csv(measurements: List[Dict[str, Any]]) -> List[List[Any]]:
    return [
        [
            row.get('id', ''),
            row.get('program_id', ''),
            row.get('run_id', ''),
            row.get('elapsed_s', ''),
            row.get('freq', ''),
            row.get('measure_ch1', ''),
            row.get('measure_ch2', ''),
            row.get('t_ch1', ''),
            row.get('t_ch2', ''),
            row.get('t_exp', ''),
            row.get('created_at', ''),
        ]
        for row in measurements
    ]


def _write_program_steps_csv(work_dir: Path, steps_resp: Dict[str, Any]) -> int:
    step_rows: List[List[Any]] = []
    for raw_row in steps_resp.get('row', []):
        parts = str(raw_row).split('^')
        if len(parts) >= 4:
            step_rows.append(parts[:4])
    _write_csv(work_dir / 'program_steps.csv', ['step_id', 't_start_k', 't_stop_k', 'minutes'], step_rows)
    return len(step_rows)


def _write_program_csv(work_dir: Path, program_info: Dict[str, Any]) -> None:
    if program_info.get('result') == 'Ok' and program_info.get('row'):
        parts = str(program_info['row']).split('^')
        if len(parts) >= 3:
            _write_csv(
                work_dir / 'program.csv',
                ['id', 'datetime', 'status'],
                [[parts[0], parts[1], parts[2]]],
            )


def _write_experiment_runs_csv(work_dir: Path, runs_resp: Dict[str, Any]) -> None:
    run_rows: List[List[Any]] = []
    for run in runs_resp.get('row', []):
        stats = run.get('measurement_stats') or {}
        run_rows.append([
            run.get('label', ''),
            run.get('run_id', ''),
            run.get('run_index', ''),
            run.get('started_at', ''),
            run.get('stopped_at', ''),
            run.get('status', ''),
            stats.get('count', 0),
        ])
    _write_csv(
        work_dir / 'experiment_runs.csv',
        ['label', 'run_id', 'run_index', 'started_at', 'stopped_at', 'status', 'sample_count'],
        run_rows,
    )


def _copy_run_charts(
    work_dir: Path,
    charts_root: Optional[str | Path],
    program_id: int,
    run_id: int,
    slug: str,
) -> List[str]:
    if not charts_root:
        return []
    src = run_chart_dir(Path(charts_root), program_id, run_id)
    if not src.is_dir():
        return []
    dest = work_dir / 'charts' / slug
    dest.mkdir(parents=True, exist_ok=True)
    copied: List[str] = []
    for png in sorted(src.glob('*.png')):
        target = dest / png.name
        shutil.copy2(png, target)
        copied.append(str(target.relative_to(work_dir)))
    return copied


def _export_runs_measurements(
    db_query: DbQueryFn,
    work_dir: Path,
    program_id: int,
    runs: List[Dict[str, Any]],
    *,
    charts_root: Optional[str | Path],
    limit: int,
    only_run_id: Optional[int] = None,
) -> Dict[str, Any]:
    total_measurements = 0
    chart_files: List[str] = []
    truncated_runs: List[Dict[str, Any]] = []
    errors: List[str] = []
    for run in runs:
        run_id = int(run.get('run_id', 0) or 0)
        if run_id <= 0:
            continue
        if only_run_id is not None and run_id != int(only_run_id):
            continue
        run_index = int(run.get('run_index', 0) or 0)
        label = str(run.get('label') or f'{program_id}.{run_index}')
        slug = _label_slug(label, program_id=program_id, run_index=run_index, run_id=run_id)
        measurements = _load_run_measurements(db_query, run_id, program_id)
        total_loaded = len(measurements)
        if limit > 0 and total_loaded > limit:
            truncated_runs.append({
                'run_id': run_id,
                'label': label,
                'exported_rows': limit,
                'total_rows': total_loaded,
            })
            measurements = measurements[:limit]
        csv_name = f'measurements_{slug}.csv'
        _write_csv(work_dir / csv_name, MEASUREMENT_HEADERS, _measurement_rows_to_csv(measurements))
        total_measurements += len(measurements)
        chart_files.extend(_copy_run_charts(work_dir, charts_root, program_id, run_id, slug))
    return {
        'measurement_count': total_measurements,
        'chart_files': chart_files,
        'truncated_runs': truncated_runs,
        'errors': errors,
    }


def _collect_db_errors(responses: Dict[str, Dict[str, Any]]) -> List[str]:
    errors: List[str] = []
    for name, resp in responses.items():
        if resp.get('result') != 'Ok':
            detail = resp.get('error', 'failed')
            errors.append(f'{name}: {detail}')
    return errors


def _finalize_zip(work_dir: Path, export_root: Path, zip_basename: str) -> str:
    zip_path = export_root / zip_basename
    import zipfile

    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in sorted(work_dir.rglob('*')):
            if file_path.is_file():
                archive.write(file_path, arcname=file_path.relative_to(work_dir).as_posix())
    shutil.rmtree(work_dir, ignore_errors=True)
    return str(zip_path)


def export_program_archive(
    db_query: DbQueryFn,
    program_id: int,
    export_dir: str,
    limit: int = 100000,
    *,
    charts_root: Optional[str | Path] = None,
) -> Dict[str, Any]:
    export_root = Path(export_dir)
    export_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    work_dir = export_root / f'program_{program_id}_{stamp}'
    work_dir.mkdir(parents=True, exist_ok=True)

    program_info = db_query({'cmd': 'get_program_by_id', 'id': program_id})
    detail_resp = db_query({'cmd': 'get_program_detail', 'id': program_id})
    steps_resp = db_query({'cmd': 'program_step_list', 'id': program_id})
    stats_resp = db_query({'cmd': 'measurement_stats', 'program_id': program_id})
    runs_resp = db_query({'cmd': 'program_run_list', 'program_id': program_id})
    e720_resp = db_query({'cmd': 'get_e720', 'id': program_id})

    db_errors = _collect_db_errors({
        'get_program_by_id': program_info,
        'get_program_detail': detail_resp,
        'program_step_list': steps_resp,
        'program_run_list': runs_resp,
    })
    if db_errors:
        shutil.rmtree(work_dir, ignore_errors=True)
        return {'ok': False, 'error': '; '.join(db_errors)}

    program_detail = detail_resp.get('row', {}) if detail_resp.get('result') == 'Ok' else {}
    runs = runs_resp.get('row', []) if runs_resp.get('result') == 'Ok' else []

    meta = {
        'program_id': program_id,
        'exported_at': datetime.now().isoformat(),
        'export_scope': 'program_all_runs',
        'measurement_row_limit': limit,
        'program': program_info,
        'program_detail': program_detail,
        'e720_config': e720_resp.get('row', {}),
        'measurement_stats': stats_resp.get('row', stats_resp),
        'experiment_runs': runs,
    }

    _write_program_csv(work_dir, program_info)
    step_count = _write_program_steps_csv(work_dir, steps_resp)
    _write_experiment_runs_csv(work_dir, runs_resp)

    per_run = _export_runs_measurements(
        db_query,
        work_dir,
        program_id,
        runs,
        charts_root=charts_root,
        limit=limit,
    )

    meta['truncated_runs'] = per_run.get('truncated_runs', [])
    if meta['truncated_runs']:
        meta['warnings'] = [
            f"Run {item['label']}: exported {item['exported_rows']} of {item['total_rows']} rows (limit {limit})"
            for item in meta['truncated_runs']
        ]
    (work_dir / 'meta.json').write_text(json.dumps(meta, indent=2, default=str), encoding='utf-8')

    zip_path = _finalize_zip(work_dir, export_root, f'program_{program_id}_{stamp}.zip')
    run_count = len(runs)
    return {
        'ok': True,
        'zip_path': zip_path,
        'measurement_count': per_run['measurement_count'],
        'step_count': step_count,
        'run_count': run_count,
        'chart_files': per_run['chart_files'],
        'truncated_runs': per_run.get('truncated_runs', []),
        'error': '',
    }


def export_run_archive(
    db_query: DbQueryFn,
    program_id: int,
    run_id: int,
    export_dir: str,
    limit: int = 100000,
    *,
    charts_root: Optional[str | Path] = None,
) -> Dict[str, Any]:
    export_root = Path(export_dir)
    export_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    run_resp = db_query({'cmd': 'program_run_get', 'run_id': int(run_id)})
    if run_resp.get('result') != 'Ok':
        return {'ok': False, 'error': run_resp.get('error', 'run not found')}
    run_row = run_resp.get('row') or {}
    if int(run_row.get('program_id', 0) or 0) != int(program_id):
        return {'ok': False, 'error': 'run does not belong to this program'}

    label = str(run_row.get('label') or f'{program_id}.{run_row.get("run_index", 0)}')
    run_index = int(run_row.get('run_index', 0) or 0)
    slug = _label_slug(label, program_id=program_id, run_index=run_index, run_id=int(run_id))
    work_dir = export_root / f'program_{program_id}_run_{slug}_{stamp}'
    work_dir.mkdir(parents=True, exist_ok=True)

    program_info = db_query({'cmd': 'get_program_by_id', 'id': program_id})
    detail_resp = db_query({'cmd': 'get_program_detail', 'id': program_id})
    steps_resp = db_query({'cmd': 'program_step_list', 'id': program_id})
    e720_resp = db_query({'cmd': 'get_e720', 'id': program_id})
    stats_resp = db_query({
        'cmd': 'measurement_stats',
        'program_id': program_id,
        'run_id': int(run_id),
    })

    db_errors = _collect_db_errors({
        'get_program_by_id': program_info,
        'get_program_detail': detail_resp,
        'program_step_list': steps_resp,
    })
    if db_errors:
        shutil.rmtree(work_dir, ignore_errors=True)
        return {'ok': False, 'error': '; '.join(db_errors)}

    program_detail = detail_resp.get('row', {}) if detail_resp.get('result') == 'Ok' else {}

    _write_program_csv(work_dir, program_info)
    step_count = _write_program_steps_csv(work_dir, steps_resp)

    per_run = _export_runs_measurements(
        db_query,
        work_dir,
        program_id,
        [run_row],
        charts_root=charts_root,
        limit=limit,
        only_run_id=int(run_id),
    )

    meta = {
        'program_id': program_id,
        'run_id': int(run_id),
        'run_label': label,
        'exported_at': datetime.now().isoformat(),
        'export_scope': 'single_run',
        'measurement_row_limit': limit,
        'program': program_info,
        'program_detail': program_detail,
        'experiment_run': run_row,
        'e720_config': e720_resp.get('row', {}),
        'measurement_stats': stats_resp.get('row', stats_resp),
        'truncated_runs': per_run.get('truncated_runs', []),
    }
    if meta['truncated_runs']:
        meta['warnings'] = [
            f"Run {item['label']}: exported {item['exported_rows']} of {item['total_rows']} rows (limit {limit})"
            for item in meta['truncated_runs']
        ]
    (work_dir / 'meta.json').write_text(json.dumps(meta, indent=2, default=str), encoding='utf-8')
    (work_dir / 'experiment_run.json').write_text(json.dumps(run_row, indent=2, default=str), encoding='utf-8')

    zip_path = _finalize_zip(work_dir, export_root, f'program_{program_id}_run_{slug}_{stamp}.zip')
    return {
        'ok': True,
        'zip_path': zip_path,
        'measurement_count': per_run['measurement_count'],
        'step_count': step_count,
        'run_count': 1,
        'run_label': label,
        'chart_files': per_run['chart_files'],
        'truncated_runs': per_run.get('truncated_runs', []),
        'error': '',
    }
