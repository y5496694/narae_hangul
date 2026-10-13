#!/usr/bin/env python3
"""Table AI Commander: 한컴오피스 표 편집 자연어 AI 명령 실행 유틸리티

사용 예시:
  python table_ai_commander.py --file 보고서.hwpx --command "'추진목적'과 '추진배경'이 있는 셀을 병합해서 '추진 개요'라고 적어줘"
  python table_ai_commander.py --file 기안문.hwpx --command "'비고' 셀을 2행으로 분할해줘"
  python table_ai_commander.py --file 양식.hwpx --command "'1행 1열'부터 '1행 3열'까지 병합하고 '통합 헤더'로 적어줘"
"""
import sys
from pathlib import Path

# Engine import
ENGINE_DIR = Path(__file__).resolve().parents[1] / "1_나래엔진" / "narae-hangul" / "scripts" / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from hangeul.table_ai_tools import main

if __name__ == "__main__":
    main()
