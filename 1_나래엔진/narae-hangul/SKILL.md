---
name: narae-hangul
description: 나래 한글의 독립 엔진으로 HWP/HWPX 문서를 한컴오피스와 0 오차로 호환 작성·편집·렌더링한다. 표 조작(셀 병합/분할/행열 삽입/크기/여백), 이미지 배치/조절, 문단/글자 정렬 서식을 완벽 지원한다.
---

# 나래 한글 (Narae Hangul) - 한컴 2024 완전 호환 AI 스킬

한컴오피스 없이 `.hwp`(HWP 5, HWP 3) 및 `.hwpx`(OWPML)를 직접 읽고, 쓰고, 편집합니다.
REA 리버스 엔지니어링 분석을 통해 한컴오피스의 내부 조판·표 계산·서식 상속 메커니즘을 나래 독립 엔진에 이식하여, **한컴오피스에서 연 화면과 나래 한글에서 연 화면 간의 오차를 0으로 유지**하고 **편집 후 저장해도 한컴에서 경고나 서식 손상 없이 완벽하게 열리도록 보장**합니다.

## 지원 주요 기능

1. **표(Table) 고급 조작 (신규/기존 문서 공통)**:
   - `merge_cells`: 표 셀 병합 (row_span, col_span, 텍스트 입력 및 정렬)
   - `split_cell`: 표 셀 분할 (rows, cols 정밀 등분 및 서브 셀 주소 재색인)
   - `set_cell_text`: 특정 셀 내용 지정/치환/추가 (텍스트 및 서식 일괄 적용)
   - `set_cell_style`: 특정 셀 또는 행/열/헤더 전체의 테두리, 채우기 배경색, 세로 정렬, 여백(padding_mm), 크기, 글자 굵기/크기 변경
   - `modify_table`: 표 위치, 정렬(`CENTER`/`LEFT`/`RIGHT`), 글자처럼 취급(`treat_as_char`), 여백 및 너비 조정
   - `insert_row` / `delete_row`: 표의 행 삽입 및 삭제
   - 표 크기(열 너비 `columns_mm`, 행 높이 `row_heights_mm`) 정밀 조절
2. **이미지(Image) 조작**:
   - `modify_image`: 이미지 크기(너비/높이), 글자처럼 취급, 수평 정렬, 여백, 데이터 교체
   - 표 셀 내부 이미지 삽입 (`blocks` 내 `image` 지원)
   - 이미지 너비·높이 mm 단위 정밀 조절 (종횡비 자동 유지 지원)
3. **글자 및 문단 정렬/서식**:
   - 가로 정렬 (`LEFT`, `CENTER`, `RIGHT`, `JUSTIFY`, `DISTRIBUTE`)
   - 세로 정렬 (`TOP`, `CENTER`, `BOTTOM`)
   - 글꼴, 크기(pt), 굵게, 기울임, 밑줄, 취소선, 글자색, 음영색, 자간, 장평, 줄간격, 들여쓰기, 문단 여백
4. **한컴오피스 무손실 보존 패치**:
   - 기존 문서 편집 시 변경되지 않은 원본 XML/CFB 스트림, 메타데이터, 바이너리 100% 무변경 보존
   - OWPML 표준 규격 준수: `mimetype` 무압축 첫 번째 저장, 네임스페이스 및 태그 구조 완전 보존

## 실행 방법

스킬 디렉토리의 `scripts/narae.py`를 Python으로 실행합니다.

```powershell
# 환경 상태 진단
python <skill-dir>/scripts/narae.py doctor

# 작업 JSON 실행 (결과 JSON 출력)
python <skill-dir>/scripts/narae.py run <job.json> --result <result.json>
```

작업 명세와 상세 JSON 스키마는 [references/jobs.md](references/jobs.md)를 참고하세요.
