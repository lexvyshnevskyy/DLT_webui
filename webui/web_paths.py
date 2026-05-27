from __future__ import annotations

from pathlib import Path


def package_dir() -> Path:
    return Path(__file__).resolve().parent


def templates_dir() -> Path:
    local = package_dir() / 'templates'
    if local.is_dir():
        return local
    try:
        from ament_index_python.packages import get_package_share_directory

        return Path(get_package_share_directory('webui')) / 'templates'
    except Exception:
        return local


def static_dir() -> Path:
    local = package_dir() / 'static'
    if local.is_dir():
        return local
    try:
        from ament_index_python.packages import get_package_share_directory

        return Path(get_package_share_directory('webui')) / 'static'
    except Exception:
        return local
