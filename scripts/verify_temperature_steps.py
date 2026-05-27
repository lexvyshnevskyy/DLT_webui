#!/usr/bin/env python3
"""Verify temperature steps and new-program fields (same rules as the Web UI).

Examples:
  echo '{"description":"Test","steps":[[1,40,100,15],[2,200,300,10]],"e720":{"sweep_mode":0,"enabled_freqs":["1000"],"range_max":10000}}' \\
    | python3 verify_temperature_steps.py

  python3 verify_temperature_steps.py program_draft.json
"""
from __future__ import annotations

import os
import sys

_PKG_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from webui.temperature_validation import _main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(_main())
