#!/usr/bin/env python3
"""Run the loopback allowlist gateway."""
from pathlib import Path
import runpy, sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
runpy.run_module("jasmine_core.tailnet_gateway", run_name="__main__")
