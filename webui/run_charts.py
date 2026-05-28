from __future__ import annotations

import math
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

DbQueryFn = Callable[[Dict[str, Any]], Dict[str, Any]]


def run_chart_dir(charts_root: Path, program_id: int, run_id: int) -> Path:
    return Path(charts_root) / f'program_{int(program_id)}' / f'run_{int(run_id)}'


def freq_chart_filename(freq: float) -> str:
    if freq is None or (isinstance(freq, (int, float)) and abs(float(freq)) < 1e-9):
        return 'measure_ch1_offline.png'
    value = float(freq)
    if math.isclose(value, round(value), rel_tol=0, abs_tol=1e-6):
        return f'measure_ch1_{int(round(value))}hz.png'
    slug = f'{value:.4f}'.rstrip('0').rstrip('.').replace('.', '_')
    return f'measure_ch1_{slug}hz.png'


def _load_run_measurements(db_query: DbQueryFn, run_id: int, program_id: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    offset = 0
    page_size = 5000
    while True:
        response = db_query({
            'cmd': 'measurement_list',
            'run_id': run_id,
            'program_id': program_id,
            'limit': page_size,
            'offset': offset,
        })
        if response.get('result') != 'Ok':
            break
        batch = response.get('row', [])
        if not batch:
            break
        if isinstance(batch[0], dict):
            rows.extend(batch)
        offset += len(batch)
        if len(batch) < page_size:
            break
    return rows


def _series_by_freq(rows: List[Dict[str, Any]]) -> Dict[float, List[Tuple[float, float]]]:
    buckets: Dict[float, List[Tuple[float, float]]] = {}
    for row in rows:
        freq = float(row.get('freq') or 0.0)
        elapsed = float(row.get('elapsed_s') or 0.0)
        measure = row.get('measure_ch1')
        if measure is None:
            continue
        buckets.setdefault(freq, []).append((elapsed, float(measure)))
    for freq in buckets:
        buckets[freq].sort(key=lambda item: item[0])
    return buckets


def generate_run_charts(
    db_query: DbQueryFn,
    charts_root: str | Path,
    run_id: int,
    program_id: int,
) -> Dict[str, Any]:
    try:
        import matplotlib

        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError as exc:
        return {'ok': False, 'error': f'matplotlib not available: {exc}'}

    rows = _load_run_measurements(db_query, run_id, program_id)
    out_dir = run_chart_dir(Path(charts_root), program_id, run_id)
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not rows:
        return {'ok': True, 'charts': [], 'message': 'no measurements'}

    elapsed = [float(r.get('elapsed_s') or 0.0) for r in rows]
    t_ch1 = [float(r.get('t_ch1') or 0.0) for r in rows]
    t_ch2 = [float(r.get('t_ch2') or 0.0) for r in rows]
    t_exp = [float(r.get('t_exp') or 0.0) for r in rows]

    fig, ax = plt.subplots(figsize=(10, 4), dpi=100)
    ax.plot(elapsed, t_ch1, label='t_ch1', linewidth=1.2)
    ax.plot(elapsed, t_ch2, label='t_ch2', linewidth=1.2)
    ax.plot(elapsed, t_exp, label='t_exp', linewidth=1.0, linestyle='--')
    ax.set_xlabel('elapsed_s')
    ax.set_ylabel('K')
    ax.set_title('Temperatures')
    ax.legend(loc='best', fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    temp_path = out_dir / 'temperature.png'
    fig.savefig(temp_path)
    plt.close(fig)

    charts = ['temperature.png']
    for freq, points in _series_by_freq(rows).items():
        if not points:
            continue
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        fig, ax = plt.subplots(figsize=(10, 4), dpi=100)
        ax.plot(xs, ys, linewidth=1.2, color='#3d8bfd')
        label = 'offline (freq=0)' if abs(freq) < 1e-9 else f'{freq:g} Hz'
        ax.set_xlabel('elapsed_s')
        ax.set_ylabel('measure_ch1')
        ax.set_title(f'measure_ch1 — {label}')
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        name = freq_chart_filename(freq)
        fig.savefig(out_dir / name)
        plt.close(fig)
        charts.append(name)

    return {'ok': True, 'charts': charts, 'dir': str(out_dir)}


def delete_run_charts(charts_root: str | Path, program_id: int, run_id: int) -> None:
    path = run_chart_dir(Path(charts_root), program_id, run_id)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
