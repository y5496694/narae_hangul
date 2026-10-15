#!/usr/bin/env python3
"""Resolve an intact Narae repository, then run its read-only viewer."""
import json
import os
from pathlib import Path
import runpy

skill = Path(__file__).resolve().parents[1]
config = skill / "runtime.json"
configured = os.environ.get("NARAE_HANGUL_ROOT")
if not configured and config.is_file():
    configured = json.loads(config.read_text(encoding="utf-8-sig")).get("repository")
candidates = [Path(configured).expanduser()] if configured else list(skill.parents)
for repository in candidates:
    launcher = repository / "6_나래뷰어/narae_viewer.py"
    if launcher.is_file():
        runpy.run_path(str(launcher), run_name="__main__")
        break
else:
    raise SystemExit("나래한글 저장소 전체가 필요합니다. NARAE_HANGUL_ROOT에 저장소 경로를 지정하세요.")
