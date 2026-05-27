#!/usr/bin/env python3
"""ROS 2 entry point — installed to lib/webui/run.py (same layout as core/run.py)."""
import os
import sys

_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from webui.node import main


if __name__ == '__main__':
    main()
