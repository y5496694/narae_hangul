#!/usr/bin/env python3
"""Standalone viewer entry point using the one bundled Narae read engine."""
from pathlib import Path
import runpy
import sys

RUNTIME = Path(__file__).resolve().parents[1] / "1_나래엔진/narae-hangul/scripts/narae.py"

if __name__ == "__main__":
    runtime = runpy.run_path(str(RUNTIME))
    raise SystemExit(runtime["run_viewer"](sys.argv[1:]))
