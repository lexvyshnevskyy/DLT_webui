from __future__ import annotations

from typing import Any


def ros_param_bool(value: Any) -> bool:
    """Parse ROS 2 parameter as bool (YAML false and string 'false' must be false)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ('true', '1', 'yes', 'on')
