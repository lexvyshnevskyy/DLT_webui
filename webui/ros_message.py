from __future__ import annotations

from typing import Any, Dict, Optional


def message_to_dict(msg: Optional[Any]) -> Optional[Dict[str, Any]]:
    if msg is None:
        return None
    result: Dict[str, Any] = {}
    fields = getattr(msg, 'get_fields_and_field_types', lambda: {})()
    for field in fields.keys():
        value = getattr(msg, field)
        if hasattr(value, 'data'):
            result[field] = value.data
        elif hasattr(value, 'sec') and hasattr(value, 'nanosec'):
            result[field] = {'sec': value.sec, 'nanosec': value.nanosec}
        elif hasattr(value, 'frame_id') and hasattr(value, 'stamp'):
            result[field] = {
                'frame_id': value.frame_id,
                'stamp': {'sec': value.stamp.sec, 'nanosec': value.stamp.nanosec},
            }
        else:
            result[field] = value
    return result
