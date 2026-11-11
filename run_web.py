#!/usr/bin/env python3
"""Convenience shortcut to launch the Web Medical Patient Monitor."""
import os
import sys

target = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_medical_monitor", "start_monitor.py")
os.execv(sys.executable, [sys.executable, target] + sys.argv[1:])
