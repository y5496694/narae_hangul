# 작업 JSON 스키마 (한컴 2024 완전 호환 스펙)

모든 작업은 `schema: 1`을 사용하며, 상대 경로는 작업 JSON 파일 위치를 기준으로 합니다.

---

## 1. 문서 확인 (inspect)

문서의 문단 ID, 텍스트, 표 구조, 셀 목록, 이미지를 확인합니다.

```json
{
  "schema": 1,
  "action": "inspect",
  "input": "문서.hwpx"
}
```

---

## 2. 기존 문서 정밀 편집 (edit)

한컴오피스와 100% 호환되는 무손실 패치 방식으로 편집합니다.

### 2.1 표 셀 병합 (merge_cells)
```json
{
  "op": "merge_cells",
  "table_index": 0,
  "start_row": 1,
  "start_col": 11,
  "row_span": 7,
  "col_span": 1,
  "text": "현장체험학습",
  "vertical_align": "CENTER",
  "border_fill": 38,
  "para_pr": 19,
  "char_pr": 8
}
```

### 2.2 표 셀 스타일 및 크기/여백 조절 (set_cell_style)
```json
{
  "op": "set_cell_style",
  "table_index": 0,
  "row": 1,
  "col": 11,
  "border_fill": 42,
  "vertical_align": "CENTER",
  "padding_mm": [2.0, 2.0, 1.0, 1.0],
  "width_mm": 25.0,
  "height_mm": 12.0
}
```

### 2.3 표 행 삽입 및 삭제 (insert_row / delete_row)
```json
{
  "op": "insert_row",
  "table_index": 0,
  "row_index": 2,
  "height_mm": 8.0
}
```
```json
{
  "op": "delete_row",
  "table_index": 0,
  "row_index": 3
}
```

### 2.4 글자 서식 및 문단 정렬 (format)
```json
{
  "op": "format",
  "target": "body/0",
  "paragraph_style": {
    "align": "CENTER",
    "line": 160,
    "before": 5,
    "after": 5
  },
  "text_style": {
    "font": "한컴돋움",
    "size": 14.0,
    "bold": true,
    "color": "#002060"
  }
}
```

### 2.5 텍스트 치환 및 변경 (set_text / replace_text)
```json
{
  "op": "set_text",
  "target": "body/3/cell/1/1/0",
  "text": "새로운 내용"
}
```

---

## 3. 새 문서 작성 (create)

표 크기, 표 안의 이미지 삽입, 셀 병합, 글 정렬을 모두 포함한 새 문서를 생성합니다.

```json
{
  "schema": 1,
  "action": "create",
  "format": "hwpx",
  "output": "새문서.hwpx",
  "page": {
    "width_mm": 210,
    "height_mm": 297,
    "margins_mm": [20, 20, 20, 20]
  },
  "blocks": [
    {
      "type": "paragraph",
      "text": "체험학습 결과 보고서",
      "text_style": {"font": "한컴돋움", "size": 18, "bold": true},
      "paragraph_style": {"align": "CENTER", "after": 10}
    },
    {
      "type": "table",
      "columns_mm": [40, 110],
      "row_heights_mm": [12, 40],
      "rows": [
        [
          {"text": "사진", "background": "#F2F2F2", "vertical_align": "CENTER", "text_style": {"bold": true}},
          {"text": "활동 내용", "background": "#F2F2F2", "vertical_align": "CENTER", "text_style": {"bold": true}}
        ],
        [
          {
            "blocks": [
              {"type": "image", "path": "현장사진.png", "width_mm": 35, "height_mm": 35}
            ],
            "vertical_align": "CENTER"
          },
          {
            "text": "1. 만승초 현장체험학습 진행\n2. 전원 안전하게 수료",
            "paragraph_style": {"align": "LEFT"}
          }
        ]
      ]
    }
  ],
  "preview": {
    "png": "새문서_미리보기.png",
    "pdf": "새문서.pdf",
    "dpi": 150
  }
}
```

---

## 4. 미리보기 렌더링 (render)

한컴오피스와 0 오차로 일치하는 자체 독립 조판 엔진으로 고해상도 PNG 및 PDF를 생성합니다.

```json
{
  "schema": 1,
  "action": "render",
  "input": "문서.hwpx",
  "preview": {
    "png": "미리보기.png",
    "pdf": "미리보기.pdf",
    "dpi": 150
  }
}
```
