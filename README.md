# 나래 한글(나래팩) (NaraeHangul) v1.2 · 엔진 0.6.0

한컴오피스에서 열리는 HWP / HWPX 문서를 AI 에이전트가 안전하게 만들고 고칠 수 있도록 만든 CLI 엔진, 스킬 문서, 템플릿 묶음입니다.

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## 만든 이유

한글 파일을 AI로 다루려면 보통 두 가지 방법뿐이었습니다.

1. 별도의 유료 AI 프로그램을 쓴다.
2. AI에게 시키되, AI가 여러 깃허브를 뒤지고 바이너리 구조를 분석해 매번 파이썬 코드를 새로 짠다.

두 번째 방법은 시간이 오래 걸리고, 수정이 번거롭고, 서식이 어긋나거나 파일이 손상되는 경우가 잦았습니다.

나래 한글은 글씨·표·그림의 생성과 편집을 미리 안정화해 두고, AI 에이전트가 읽고 바로 쓸 수 있는 형태(CLI + 스킬 문서 + 템플릿)로 묶어 둔 패키지입니다. AI는 바이너리를 분석할 필요 없이 정해진 명령만 호출하면 됩니다.

## 특징

- **표와 서식은 두고 내용만 수정**: 표의 구조와 서식을 건드리지 않고 필요한 셀의 내용만 바꾸는 방식을 기본으로 합니다.
- **템플릿 저장 후 재사용**: 자주 쓰는 양식을 JSON 템플릿으로 저장해 두면 같은 형식의 문서를 반복해서 만들 수 있습니다.
- **웹 AI에서도 사용 가능**: ChatGPT, Claude 웹 대화창에 이 패키지(또는 GitHub 주소)와 한글 파일을 첨부하면, AI가 `SKILL.md`를 읽고 같은 방식으로 작업합니다.
- **독립 편집과 한컴 실행 경로 제공**: 보존 가능한 텍스트·서식은 원본 XML/바이너리를 수정합니다. HWP의 고급 표 편집은 설치된 한컴 COM을 사용합니다.
- **한컴 전체 COM 인터페이스**: `hancom_capabilities`로 실제 메서드·파라미터셋·액션을 조회하고, `hancom` 작업으로 생성·편집합니다. 한컴 2024에서 선언 항목 214개, 파라미터셋 212개, 생성 가능한 공식 액션 식별자 849개를 확인했습니다. 사용법은 [한컴 인터페이스 안내](1_나래엔진/narae-hangul/references/hancom.md)에 있습니다. 모든 기능의 효과를 검증한 것은 아닙니다.
- **자연어 명령 지원**: "'비고' 셀 테두리 없애줘" 같은 문장을 작업 목록으로 변환해 실행합니다.

## 지원 기능

**읽기 전용 나래한글 뷰어**: 기존 에디터 읽기 모드를 분리해 HWP/HWPX 화면 보기, 쪽별 텍스트 읽기, PDF·PNG 출력, 한컴 기준과의 전체 쪽 비교를 제공합니다. [뷰어 실행](6_나래뷰어/README.md), [뷰어 CLI](1_나래엔진/narae-hangul/references/viewer.md), [읽기·검증 스킬](2_나래스킬/narae-viewer/SKILL.md)을 확인하세요. 기본 독립 렌더링과 명시적 한컴 기준 출력을 구분하며, 모든 문서의 시각적 일치는 아직 보장하지 않습니다.

| 구분 | 내용 |
| :--- | :--- |
| 표 | 셀 병합 / 분할, 셀 내용 지정, 행 삽입 / 삭제, 셀 크기·여백, 4면 개별 테두리, 배경색 |
| 글자·문단 | 굵게, 글자 크기·색, 정렬 |
| 표·그림 위치 | 글자처럼 취급, 가로 정렬, 그림 크기와 비율 조정 |
| 문서 | 새 문서 생성, 기존 문서 확인(inspect), 쪽수 검증 |
| 설치된 한컴 | 원시 액션, COM 메서드·속성, 중첩 개체·파라미터셋, 누름틀, 그림 개체, 저장 후 내용·서식 검증 |

> 작업 JSON(`ops`)이 지원하는 전체 연산은 [`1_나래엔진/narae-hangul/references/jobs.md`](1_나래엔진/narae-hangul/references/jobs.md)에 정리되어 있습니다. 자연어 명령은 그중 일부(병합, 분할, 내용 변경, 굵게, 정렬, 배경색, 테두리, 표·그림 정렬)만 해석합니다. 그 외 작업은 `--ops-json`으로 직접 지정하세요.

## 구성

```
├── 1_나래엔진/        HWP/HWPX 읽기·편집·생성 엔진 (narae-hangul)
├── 2_나래스킬/        AI 에이전트용 지침서 (SKILL.md)
├── 3_형식템플릿/      양식 템플릿 11종 (JSON)
├── 4_문구템플릿/      문구집 7종 (Markdown)
├── 5_유틸리티/        보조 스크립트와 규격 문서
├── 6_나래뷰어/        읽기 전용 HWP/HWPX 뷰어와 CLI
└── 나래한글_안내.txt   패키지 상세 안내
```

**형식 템플릿 (11종)**: 보고서 3쪽 세트, 기안문, 현장체험학습 계획서, 연수 결과보고서, 주간 지원인력 시간표, 교수학습 지도안, 체크리스트·평가표, 연구대회 보고서, 보도자료, 가정통신문·신청서, 회의록·협의록

**문구 템플릿 (7종)**: 개조식 기호, 추진목적·기대효과, 안전지도·비상대응, 결재·협조 안내, 특수교육 개별화 지원, 생활지도 사안처리, 가정통신문 인사말

## 사용 방법

> **필수: 포함된 CLI를 실제 실행하세요.** 나래한글로 HWP/HWPX 문서를 생성·수정·편집할 때는 나래팩의 내용만 참고하여 AI가 파일을 직접 조립·수정해서는 안 됩니다. 반드시 포함된 `narae.py` 또는 작업에 맞는 `table_ai_commander.py`를 구동하고 실행 결과와 출력 파일을 검증해야 합니다. CLI를 우회하면 문서 구조·서식 오류가 생길 수 있습니다. CLI를 실행할 수 없는 환경에서는 직접 생성·편집으로 대체하지 말고, 작업 JSON이나 실행 안내만 제공했으며 문서 작업은 아직 미실행임을 명시하세요. 자세한 필수 규칙은 [`2_나래스킬/SKILL.md`](2_나래스킬/SKILL.md)를 따릅니다.

### 웹 AI(ChatGPT / Claude)에서

1. 대화창에 이 패키지의 zip 파일(또는 GitHub 주소)과 수정할 한글 파일을 함께 올립니다.
2. 예: "2_나래스킬/SKILL.md를 읽고, 첨부한 문서의 표 서식은 그대로 두고 2행의 내용만 이 문구로 바꿔줘."

> 웹 AI는 코드 실행 환경이 제공되는 경우에 한해 직접 파일을 수정할 수 있습니다. 실행 환경이 없으면 AI가 작업 JSON만 만들어 주고, 사용자가 로컬에서 실행해야 합니다.

### 코딩 에이전트(Hermes Agent / Codex / Claude Code)에서

터미널과 파일을 다루는 에이전트는 웹 AI보다 간단합니다. 패키지를 작업 폴더에 두고 `2_나래스킬/SKILL.md`를 읽게 하면, 에이전트가 직접 CLI를 실행해 파일을 수정하고 결과를 확인합니다.

1. 이 저장소를 clone하거나 zip을 풀어 작업 폴더에 둡니다.
2. 에이전트에게 지시합니다.
   ```
   2_나래스킬/SKILL.md를 먼저 읽고, 그 규칙대로 보고서.hwpx의 표 서식은 유지한 채
   '추진목적' 셀 내용만 아래 문구로 바꿔줘. 원본은 덮어쓰지 말고 결과.hwpx로 저장해줘.
   ```
3. 매번 지시하기 번거로우면 에이전트가 자동으로 읽는 지침 파일(`CLAUDE.md`, `AGENTS.md` 등)에 "한글 파일 작업은 `2_나래스킬/SKILL.md`를 따른다"는 한 줄을 적어 둡니다.

| 에이전트 | 연결 방법 |
| :--- | :--- |
| Claude Code | 프로젝트의 `CLAUDE.md`에 SKILL.md 경로를 적거나, `SKILL.md`를 Claude Code의 스킬 폴더(`.claude/skills/`)에 넣습니다. |
| Codex | 프로젝트의 `AGENTS.md`에 SKILL.md 경로를 적습니다. |
| Hermes Agent | 에이전트의 스킬 또는 컨텍스트 파일로 `SKILL.md`를 등록하거나, 첫 지시에서 읽게 합니다. |

> 스킬 폴더의 위치와 이름은 에이전트 버전마다 다를 수 있으니, 각 에이전트의 문서에서 확인해 주세요. 어떤 경우든 에이전트가 `SKILL.md`를 읽고 `table_ai_commander.py` / `narae.py`를 실행할 수 있으면 같은 방식으로 동작합니다. Python 3.10 이상이 설치되어 있어야 합니다.

### 로컬에서

필요한 것: Python 3.10 이상. 화면 렌더링·PDF 검증 기능을 쓸 때만 `pip install PySide6`이 필요합니다.

Windows 한컴 경로는 `pywin32`와 승인된 파일 접근 모듈이 필요합니다. `narae.py setup`은 Windows에 pywin32를 설치하며, `narae.py doctor --hancom`으로 한컴과 모듈 등록을 확인합니다. 공식 DLL을 명시적으로 등록하려면 `setup --hancom-module <DLL 경로>`를 사용하세요. 설치·진단 상세는 [hancom.md](1_나래엔진/narae-hangul/references/hancom.md)를 따릅니다.

**자연어로 표 편집** (`.hwp`, `.hwpx` 모두 가능)

```bash
python 5_유틸리티/table_ai_commander.py --file 보고서.hwpx --command "'비고' 셀 테두리 없애줘"
python 5_유틸리티/table_ai_commander.py --file 보고서.hwpx --command "'비고' 셀 내용을 '해당 없음'으로 변경하고 글자 굵게 해줘"
python 5_유틸리티/table_ai_commander.py --file 양식.hwp --command "헤더 행 전체 글자 굵게 하고 가운데 정렬해줘" --output 결과.hwp
```

`--output`을 생략하면 원본 파일을 직접 덮어씁니다. 중요한 파일은 먼저 복사해 두세요.

**작업 JSON으로 편집·생성**

```bash
python 1_나래엔진/narae-hangul/scripts/narae.py run job.json
```

작업 JSON 작성법은 [`jobs.md`](1_나래엔진/narae-hangul/references/jobs.md)를 참고하세요.

**보조 도구** (HWPX 전용)

```bash
# 기존 문서에서 템플릿 추출
python 5_유틸리티/extract_template.py 원본.hwpx 템플릿.json

# 쪽수 검증 (--export-pdf는 선택)
python 5_유틸리티/verify_and_export.py 문서.hwpx
```

이 보조 도구들은 현재 HWPX만 지원하며, 계속되는 업데이트를 통해 HWP도 지원할 예정입니다.

## 알아둘 점

- 한컴오피스 기준으로 작성했고, 한컴이 설치되지 않은 환경에서도 편집은 가능합니다. 다만 실제 쪽수는 한컴에서 열어 확인하는 것이 가장 정확합니다. 내장 뷰어는 추정 레이아웃이라 한컴과 쪽 나뉨이 다를 수 있습니다.
- 본문 영역 높이는 262mm를 기준으로 하며, 줄 넘김이 생기면 글자 크기 대신 자간을 -5% ~ -10% 범위에서 줄이는 방식을 권장합니다. 자세한 내용은 `5_유틸리티/hancom_layout_guide.md`를 참고하세요.
- 표·셀 조작은 [REA](https://github.com/morluto/rea) 기반으로 정리한 규격(`5_유틸리티/hancom_rea_spec.md`)을 따릅니다.
- 문서마다 내부 구조가 달라, 모든 HWP/HWPX 파일에서 동일하게 동작한다고 보장하지는 않습니다. 새 양식에 적용할 때는 결과를 한컴에서 열어 확인해 주세요.
- `success`는 실행·저장 검사의 성공입니다. 원시 기능의 의도한 효과는 작업의 `verify`로 검사하고 `requested_effects_verified`를 확인하세요. 전체 기능·시각적 무결점 보장을 의미하지 않습니다. [REA 분석과 검증 범위](5_유틸리티/hancom_rea_findings.md)를 참고하세요.

## 회귀 검증

```powershell
python -m unittest discover -s tests -v
# 설치된 한컴과 승인된 파일 접근 모듈을 사용하는 실제 검사:
$env:NARAE_RUN_HANCOM_TESTS = "1"
python -m unittest discover -s tests -v
```

## 라이선스

MIT License
