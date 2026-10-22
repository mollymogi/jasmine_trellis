#!/usr/bin/env python3
"""Fixed operator release; isolated imports ignore project/PYTHONPATH code."""
from pathlib import Path
import runpy,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
runpy.run_module('jasmine_core.capture.install_codex',run_name='__main__')
