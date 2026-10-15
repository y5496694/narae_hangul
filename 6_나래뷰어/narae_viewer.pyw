"""Double-click entry point without a console on Windows."""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).with_suffix(".py")), run_name="__main__")
