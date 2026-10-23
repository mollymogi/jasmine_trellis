#!/usr/bin/env python3
"""Run this exact release with the existing dependency runtime, isolated from projects."""
from pathlib import Path
import runpy,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
runpy.run_module('jasmine_core.cli',run_name='__main__')
