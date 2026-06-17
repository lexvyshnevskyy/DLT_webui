from __future__ import annotations

from typing import Any, Dict, List, Optional


def measure_device_label(source: str) -> str:
    normalized = (source or 'e720').strip().lower()
    return 'IM3536' if normalized == 'im3536' else 'E7-20'


def infer_measure_device_label(frame_id: str, configured_source: str = 'e720') -> str:
    fid = str(frame_id or '')
    if fid.startswith('im3536'):
        return 'IM3536'
    if fid.startswith('e720') or fid.startswith('measure_device'):
        return 'E7-20'
    return measure_device_label(configured_source)


def format_frequency(value: float, dimension: str = 'Hz') -> str:
    value = float(value or 0)
    if value < 1000:
        return f'{value:.0f} {dimension}'
    if value < 1_000_000:
        return f'{value / 1000:.0f} k{dimension}'
    return f'{value / 1_000_000:.0f} M{dimension}'


def e720_from_msg(msg: Any) -> Dict[str, Any]:
    if msg is None:
        return {'online': False}

    def field(name: str) -> Any:
        value = getattr(msg, name, None)
        return getattr(value, 'data', value)

    frame_id = ''
    if hasattr(msg, 'header') and hasattr(msg.header, 'frame_id'):
        frame_id = str(msg.header.frame_id)

    online = not str(frame_id).endswith('_offline')
    return {
        'online': online,
        'frame_id': frame_id,
        'offset': float(field('offset') or 0.0),
        'level': float(field('level') or 0.0),
        'frequency': float(field('frequency') or 0.0),
        'limit': str(field('limit') or ''),
        'imparam': str(field('imparam') or ''),
        'secparam': str(field('secparam') or ''),
        'firstvalue': float(field('firstvalue') or 0.0),
        'secondvalue': float(field('secondvalue') or 0.0),
    }


def e720_summary_text(data: Dict[str, Any], *, measure_source: str = 'e720') -> str:
    if not data.get('online', False) and data.get('frame_id') == '':
        label = measure_device_label(measure_source)
        return f'{label}: waiting for data...'
    frame_id = str(data.get('frame_id', '') or '')
    device_label = infer_measure_device_label(frame_id, measure_source)
    status = 'ONLINE' if data.get('online') else 'OFFLINE'
    lines = [
        f'{device_label} — Status: {status} ({frame_id})',
        f'Primary:   {data.get("imparam", "")} = {data.get("firstvalue", 0):.6g}',
        f'Secondary: {data.get("secparam", "")} = {data.get("secondvalue", 0):.6g}',
        f'Frequency: {format_frequency(float(data.get("frequency", 0) or 0))}',
    ]
    if device_label == 'E7-20':
        lines.extend([
            f'Level:     {float(data.get("level", 0) or 0):.2f}',
            f'Offset:    {float(data.get("offset", 0) or 0):.2f}',
            f'Range:     {data.get("limit", "")}',
        ])
    return '\n'.join(lines)


def measure_table_headers(measure_source: str) -> List[str]:
    if measure_device_label(measure_source) == 'IM3536':
        return ['Online', 'Param 1', 'Value 1', 'Param 2', 'Value 2', 'Frequency']
    return ['Online', 'Ch1 param', 'Ch1', 'Ch2 param', 'Ch2', 'Freq', 'Level', 'Offset', 'Range']


def e720_table_row(data: Dict[str, Any], *, measure_source: str = 'e720') -> list:
    frame_id = str(data.get('frame_id', '') or '')
    device_label = infer_measure_device_label(frame_id, measure_source)
    row = [
        'yes' if data.get('online') else 'no',
        data.get('imparam', ''),
        data.get('firstvalue', 0),
        data.get('secparam', ''),
        data.get('secondvalue', 0),
        format_frequency(float(data.get('frequency', 0) or 0)),
    ]
    if device_label == 'E7-20':
        row.extend([
            float(data.get('level', 0) or 0),
            float(data.get('offset', 0) or 0),
            data.get('limit', ''),
        ])
    return row
