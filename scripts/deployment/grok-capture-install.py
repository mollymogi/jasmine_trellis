#!/usr/bin/env python3
"""Fixed release installer; dashboard supplies no executable/project arguments."""
from pathlib import Path
import runpy,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
runpy.run_module('jasmine_core.capture.install_grok',run_name='__main__')
