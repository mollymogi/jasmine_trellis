#!/usr/bin/env python3
"""Pinned operator release only; isolated Python ignores project/PYTHONPATH imports."""
from pathlib import Path
import runpy,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
runpy.run_module('jasmine_core.capture.grok_session',run_name='__main__')
