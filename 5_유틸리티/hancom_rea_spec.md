# 한컴오피스 단위·서식 참고 규격

이 문서는 단위·서식 설정의 참고 자료입니다. 한컴의 공식 인증이나 전체 기능 일치 검증서가 아닙니다. 실제 REA/Ghidra/Frida 분석 결과와 검증 범위는 [hancom_rea_findings.md](hancom_rea_findings.md), 설치된 한컴의 액션·파라미터 조회는 [hancom.md](../1_나래엔진/narae-hangul/references/hancom.md)를 따릅니다.

---

## 1. 기본 단위계 및 환산 공식 (Unit System)

| 단위 | HWPUNIT 환산 | 비고 |
| :--- | :--- | :--- |
| **1 inch** | 7,200 HWPUNIT | 기본 기준치 |
| **1 mm** | 약 283.465 HWPUNIT | `MiliToHwpUnit(mm)` (7200 / 25.4) |
| **1 pt** | 100 HWPUNIT | `PointToHwpUnit(pt)` (글자 크기 등) |

---

## 2. 글자 모양 (`CharShape` / `<hh:charPr>`)

| 속성 | 한컴 2024 파라미터 / XML | 허용 범위 및 기본값 | 비고 |
| :--- | :--- | :--- | :--- |
| **글자 크기** | `Height` / `@height` | 1 pt ~ 1,000 pt (100 ~ 100,000 HWPUNIT) | 본문 표준 10pt (1,000 HWPUNIT) |
| **자간** | `SpacingHangul` 등 언어별 속성 / `<hh:spacing>` | **-50% ~ +50%** | **줄넘김 방지 표준 -5% ~ -10%** |
| **장평** | `RatioHangul` 등 언어별 속성 / `<hh:ratio>` | **50% ~ 200%** | 표준 100% |
| **굵기** | `Bold` / `<hh:bold/>` | 0 (False) 또는 1 (True) | 제목 및 강조 |
| **기울임** | `Italic` / `<hh:italic/>` | 0 또는 1 | 참고 및 영문 |
| **밑줄** | `Underline` / `<hh:underline>` | 위치(BOTTOM), 모양(SOLID), 색상 | 서식 강조 |
| **글자색** | `TextColor` / `@textColor` | `#RRGGBB` / Win32 COLORREF (`0x00BBGGRR`) | 기본 `#000000` |

---

## 3. 문단 모양 (`ParagraphShape` / `<hh:paraPr>`)

| 정렬 방식 | 한컴 정수값 | OWPML 속성값 | 비고 |
| :--- | :---: | :--- | :--- |
| **양쪽 정렬** | 0 | `JUSTIFY` | 공문서/보고서 본문 기본값 |
| **가운데 정렬** | 3 | `CENTER` | 표 헤더, 표지, 대주제 기본값 |
| **왼쪽 정렬** | 1 | `LEFT` | 개조식 항목 기본값 |
| **오른쪽 정렬** | 2 | `RIGHT` | 날짜, 서명, 발행처 기본값 |
| **배분 정렬** | 4 | `DISTRIBUTE` | 양 끝 배분 |

* **줄간격 (Line Spacing)**: 기본 `PERCENT` 160% (교육/행정 공문서 표준)

COM 액션은 `ParagraphShape`, 파라미터셋은 `ParaShape`, 정렬 항목은 `AlignType`입니다. `Align`은 한컴에서 조용히 무시될 수 있으므로 사용하지 않습니다.
* **문단 여백 (Margin)**: `intent`(들여쓰기), `left`, `right`, `prev`(앞), `next`(뒤) (단위: HWPUNIT)

---

## 4. 표 및 셀 모양 (`Table` & `Cell`)

### 4.1 선 종류 (Line Type: `HwpLineType` / `<hh:border>`)
| 선 종류 명칭 | 한컴 열거형 값 | OWPML XML 속성 | 시각적 용도 |
| :--- | :---: | :--- | :--- |
| **None** | 0 | `NONE` | 투명선 (표 좌/우 외곽선 제거 시) |
| **Solid** | 1 | `SOLID` | 표준 실선 (가장 널리 사용) |
| **Dash** | 2 | `DASH` | 파선 |
| **Dot** | 3 | `DOT` | 점선 |
| **DashDot** | 4 | `DASH_DOT` | 1점 쇄선 |
| **DashDotDot** | 5 | `DASH_DOT_DOT` | 2점 쇄선 |
| **Circle** | 7 | `CIRCLE` | 원형 점선 |
| **DoubleSlim** | 8 | `DOUBLE_SLIM` | **이중 실선 (표 상단/하단 헤더 마감)** |
| **SlimThick** | 9 | `SLIM_THICK` | 가는선-굵은선 |
| **ThickSlim** | 10 | `THICK_SLIM` | 굵은선-가는선 |

### 4.2 선 굵기 (Line Width: `HwpLineWidth` / `<hh:border>`)
| 한컴 규격 | 열거형 값 | OWPML 표기 | 권장 용도 |
| :--- | :---: | :--- | :--- |
| **0.1 mm** | 0 | `0.1 mm` | 표 내부 보조 구분선 |
| **0.12 mm** | 1 | `0.12 mm` | **표 내부 표준 실선** |
| **0.2 mm** | 3 | `0.2 mm` | 표 외곽 테두리 강조선 |
| **0.3 mm** | 5 | `0.3 mm` | 구획 분할선 |
| **0.5 mm** | 7 | `0.5 mm` | 대제목 하단 언더라인 |
| **1.0 mm ~ 5.0 mm**| 10 ~ 15 | `1.0 mm` ~ `5.0 mm` | 특수 강조 |

### 4.3 셀 수직 정렬 (`VertAlign` / `<hp:subList vertAlign="...">`)
* `TOP`: 상단 맞춤
* `CENTER`: **수직 중앙 맞춤 (공문서/보고서 표 표준)**
* `BOTTOM`: 하단 맞춤

### 4.4 4방향 개별 테두리 및 면 설정 (Individual Borders)
* **상단(Top)**: 헤더 구분선 (표준 `0.2 mm` Solid)
* **하단(Bottom)**: 헤더 마감 이중선 (`0.5 mm` DoubleSlim) 또는 마감 실선 (`0.2 mm` Solid)
* **좌·우(Left/Right)**: 현대식 보고서/공문서 표준 스타일 적용 시 외곽선 투명(`NONE`) 처리
* **내부(Inside)**: 횡/종 보조 구분선 (`0.12 mm` Solid)

### 4.5 표 위치 및 배치 규격 (`<hp:tbl>` / `TableProperty`)
* **글자처럼 취급 (`treatAsChar`)**: 기본값 `1` (True) - 문서 흐름에 맞추어 레이아웃 깨짐 방지
* **표 정렬 (`horzAlign`)**: `CENTER`(가운데 정렬 표준), `LEFT`, `RIGHT`
* **바깥 여백 (`outMargin`)**: 상/하/좌/우 HWPUNIT (기본 `0`)
* **표 너비/높이 (`sz`)**: 가용 본문 폭 170mm (48,189 HWPUNIT) 기준 등분 계산

---

## 5. 그림 및 그래픽 개체 규격 (`Picture` & `<hp:pic>`)

| 속성 | 한컴 2024 파라미터 / XML | 규격 및 권장값 | 비고 |
| :--- | :--- | :--- | :--- |
| **글자처럼 취급** | `TreatAsChar` / `@treatAsChar` | `1` (True) 또는 `0` (False) | 공문서 첨부 사진은 `1` 권장 |
| **수평 정렬** | `HorzAlign` / `@horzAlign` | `CENTER`, `LEFT`, `RIGHT` | 기본 `CENTER` |
| **개체 크기** | `Width`, `Height` / `<hp:sz>` | HWPUNIT 환산치 (`width_mm * 283.465`) | 비율 유지 리사이징 |
| **바깥 여백** | `OutMargin` / `<hp:outMargin>` | `left`, `right`, `top`, `bottom` | 기본 상/하 2mm, 좌/우 0mm |
| **이미지 데이터** | `BinData` / `<hp:img>` | `BinData/BINxxxx.png` (HWPX), 바이너리 스트림 | PNG/JPG 무손실 임베딩 |

---

## 6. REA 기반 AI 액션 인터페이스 (Action & ParameterSet)

| AI 자연어 의도 | 한컴 오토메이션 액션 | HWPX OWPML 처리 |
| :--- | :--- | :--- |
| **셀 병합** | `TableCellBlock` → `TableCellBlockExtend` → `TableMergeCell` | `<hp:tc>`의 `rowSpan`, `colSpan` 갱신 및 내부 포함 셀 합병 제거 |
| **셀 분할** | `TableCellBlock` → `TableSplitCell` (`Rows`, `Cols`, `Merge`) | 너비/높이 HWPUNIT 정밀 등분 및 서브 `<hp:tc>` 연속 생성/주소 재색인 |
| **셀 내용 지정/교체** | 실제 표·셀에 진입 → `MoveListBegin` → 셀 안 `SelectAll` → `Delete` → `InsertText` 액션 | `<hp:tc>/<hp:subList>/<hp:p>` 내 텍스트 치환 및 `<hh:charPr>` 적용 |
| **셀 서식/테두리** | `CellBorderFill` (`HCellBorderFill` pset: Left/Right/Top/Bottom) | `<hh:borderFill>` 개별 border 생성 및 `@borderFillIDRef` 연결 |
| **글자 서식 (굵기/크기)** | `CharShape` (`HCharShape` pset: Height, Bold, Spacing) | `<hh:charPr>` 헤더 생성 및 문단 내 `<hp:run>` 링크 |
| **표 위치/정렬 수정** | `TablePropertyDialog` / `ShapeObject` (Align, TreatAsChar) | `<hp:tbl>`의 `<hp:pos>` 및 `<hp:outMargin>` 속성 직접 수정 |
| **그림 크기/위치 수정** | `ShapeObject` (Width, Height, TreatAsChar, HorzAlign) | `<hp:pic>`의 `<hp:sz>`, `<hp:pos>`, `<hp:outMargin>` 속성 직접 수정 |
| **행 추가/삭제** | `TableInsertUpperRow` / `TableInsertLowerRow` / `TableDeleteRow` | `<hp:tr>` 요소 추가/삭제 및 내부 `rowAddr` 전체 재인덱싱 |

