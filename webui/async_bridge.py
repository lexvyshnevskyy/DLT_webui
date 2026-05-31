"""Run blocking ROS/DB node work off the FastAPI asyncio event loop."""

from __future__ import annotations

import asyncio
from typing import Callable, TypeVar

T = TypeVar('T')


async def run_blocking(func: Callable[..., T], /, *args, **kwargs) -> T:
    return await asyncio.to_thread(func, *args, **kwargs)
