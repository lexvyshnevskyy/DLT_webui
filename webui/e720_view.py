from __future__ import annotations

from typing import Any, Dict, Optional


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

    online = frame_id != 'e720_offline'
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


def e720_summary_text(data: Dict[str, Any]) -> str:
    if not data.get('online', False) and data.get('frame_id') == '':
        return 'E7-20: waiting for data...'
    status = 'ONLINE' if data.get('online') else 'OFFLINE'
    lines = [
        f'Status: {status} ({data.get("frame_id", "")})',
        f'Primary:   {data.get("imparam", "")} = {data.get("firstvalue", 0):.6g}',
        f'Secondary: {data.get("secparam", "")} = {data.get("secondvalue", 0):.6g}',
        f'Frequency: {format_frequency(float(data.get("frequency", 0) or 0))}',
        f'Level:     {float(data.get("level", 0) or 0):.2f}',
        f'Offset:    {float(data.get("offset", 0) or 0):.2f}',
        f'Range:     {data.get("limit", "")}',
    ]
    return '\n'.join(lines)


def e720_table_row(data: Dict[str, Any]) -> list:
    return [
        'yes' if data.get('online') else 'no',
        data.get('imparam', ''),
        data.get('firstvalue', 0),
        data.get('secparam', ''),
        data.get('secondvalue', 0),
        format_frequency(float(data.get('frequency', 0) or 0)),
        float(data.get('level', 0) or 0),
        float(data.get('offset', 0) or 0),
        data.get('limit', ''),
    ]
