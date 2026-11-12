#!/usr/bin/env python3
"""Run the loopback allowlist gateway for label com.jasminecore.v1.local.tailnet-gateway.

The matching plist ships Disabled and is not bootstrapped here. KeepAlive matches
com.jasminecore.v1.local.core. This script does not configure tailscale serve.
"""
from pathlib import Path
import runpy, sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
runpy.run_module("jasmine_core.tailnet_gateway", run_name="__main__")
