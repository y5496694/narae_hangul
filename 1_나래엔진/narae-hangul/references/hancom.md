# 설치된 한컴을 사용하는 전체 COM 인터페이스

엔진 0.6.0은 Windows의 실제 한컴 엔진을 통해 HWP/HWPX 문서를 만들고 편집할 수 있습니다. 제한된 연산 목록 밖의 기능은 액션·메서드·속성·중첩 COM 개체로 접근합니다. 한컴이 설치되지 않은 환경의 독립 엔진과 지원 범위가 다릅니다.

한컴 2024 `13, 0, 0, 711`에서 COM 선언 항목 214개, 파라미터셋 212개, 생성 가능한 공식 액션 식별자 849개를 조회했습니다. 이 수치는 모든 액션의 실행·효과를 검증했다는 뜻이 아닙니다. 기능은 한컴 버전, 캐럿 위치, 문서 보호 상태에 따라 사용 가능 여부가 달라집니다. 독립 조판 엔진의 전체 기능 일치와 픽셀 일치는 보장하지 않습니다.

작업은 별도 문서를 소유하고 종료 시 그 문서만 닫습니다. 한컴 프로세스가 남을 수 있습니다. 한컴의 활성 문서·캐럿 상태는 공유되므로 작업을 순차 실행하고 실행 중 직접 문서를 조작하지 않습니다.

## 준비와 진단

```powershell
python scripts/narae.py setup
python scripts/narae.py doctor --hancom
# 한컴 공식 FilePathCheckDLL을 명시적으로 등록할 때만:
python scripts/narae.py setup --hancom-module C:/approved/FilePathCheckerModuleExample.dll
```

DLL은 [한컴 공식 개발 가이드](https://developer.hancom.com/hwpautomation)에서 받습니다. setup은 지정한 DLL을 전용 런타임 폴더에 복사하고, 현재 사용자 레지스트리에 `NaraeHangulFilePathChecker`라는 별도 값으로 등록합니다. 기존 `FilePathCheckerModule` 값과 `Uses` 설정을 바꾸지 않습니다. 한컴 프로그램과 DLL의 비트 수가 일치해야 합니다. `pywin32`는 Windows setup 의존성에 포함됩니다.

다른 이름으로 등록한 승인된 모듈을 사용하면 `NARAE_HANCOM_SECURITY_MODULE` 환경 변수에 **DLL 경로가 아닌 레지스트리 값 이름**을 지정합니다. 기본 검색 순서는 `NaraeHangulFilePathChecker`, `FilePathCheckerModule`입니다. 모듈 등록 실패 시 숨겨진 승인창에서 무한 대기하지 않고 실패합니다. 사용자가 직접 승인창을 처리하는 작업은 `action: hancom`에 `visible: true`를 지정합니다.

## 먼저 조회하기

```json
{
  "action": "hancom_capabilities",
  "action_names": ["InsertText", "ParagraphShape", "PageSetup", "EquationCreate"]
}
```

결과에는 설치된 COM 메서드의 인자 이름, 읽기/쓰기 속성, 파라미터셋의 선언, 지정한 액션의 기본값과 현재 문맥의 활성 상태가 포함됩니다. 기존 문서의 문맥에서 조사하려면 `input`도 지정합니다. `CreateAction`이 가능해도 현재 캐럿에서 실행 가능하다는 뜻은 아닙니다.

## 생성·편집 작업

`action: hancom`에 `input`이 없으면 새 문서, 있으면 기존 문서 편집입니다. 출력 확장자로 HWP/HWPX를 선택합니다. input/output의 상대 경로는 작업 JSON 위치를 기준으로 합니다. 원시 COM 인자의 파일 경로는 한컴에 그대로 전달되므로 절대 경로를 사용합니다. 기존 출력 덮어쓰기는 `overwrite_output: true`, 원본 덮어쓰기는 `overwrite_source: true`를 명시해야 합니다. 원본 SHA-256을 `expected_input_sha256`으로 잠글 수 있습니다.

```json
{
  "action": "hancom",
  "output": "보고서.hwp",
  "operations": [
    {"op": "hancom_action", "action": "PageSetup", "parameters": {
      "PageDef": {"$set": "PageDef", "items": {"LeftMargin": 5669, "RightMargin": 5669}}
    }},
    {"op": "hancom_action", "action": "InsertText", "parameters": {"Text": "보고서 제목\n본문 내용"}},
    {"op": "hancom_method", "name": "CreateField", "args": ["", "", "작성자"]},
    {"op": "hancom_method", "name": "PutFieldText", "args": ["작성자", "홍길동"]}
  ],
  "verify": [
    {"kind": "text_contains", "text": "본문 내용"},
    {"kind": "field_text", "field": "작성자", "expected": "홍길동"}
  ]
}
```

파라미터 없는 액션은 `parameters`를 생략하면 `HAction.Run`을 사용합니다. 파라미터가 있으면 `CreateAction → CreateSet → GetDefault → SetItem → Execute`를 사용합니다. 원시 액션의 단위는 한컴의 HWPUNIT 및 해당 파라미터 규격입니다. 1mm는 7200/25.4 HWPUNIT, 1pt는 100 HWPUNIT입니다. InsertText의 LF는 한컴이 요구하는 CRLF로 변환합니다.

없는 파라미터 이름은 실행 전에 거부합니다. 예를 들어 문단 정렬은 `Align`이 아닌 `ParaShape.AlignType`이며, 0=양쪽, 1=왼쪽, 2=오른쪽, 3=가운데, 4=배분, 5=공백 배분입니다. 글자 자간·장평은 언어별 `SpacingHangul`, `RatioHangul` 등의 항목입니다.

## 표와 그림

기존 `merge_cells`, `split_cell`, `set_cell_text`, `set_cell_style`, `insert_row`, `delete_row`, `modify_table`, `modify_image`의 위치·크기 조정은 같은 작업 안에서 사용할 수 있습니다. `hancom_move_cell`은 뒤따르는 원시 액션의 캐럿을 지정합니다.

```json
{"op": "hancom_move_cell", "table_index": 1, "row": 0, "col": 0}
```

표·셀 인덱스는 0부터 시작합니다. 텍스트 셀 선택자는 해당 표 안에서 정확히 한 셀에 일치해야 합니다. 병합 영역이 기존 병합 셀을 가로지르면 거부합니다. HWPX의 `char_pr`, `para_pr`, `border_fill` 참조 ID는 COM 서식 값으로 사용할 수 없습니다. 셀 분할의 `texts`는 결과 셀 수와 같은 길이의 배열이며 행 순서로 채웁니다.

## COM 개체와 참조

`hancom_method`는 조회한 인자 순서의 배열을 받습니다. `hancom_get`/`hancom_set`은 속성을 읽고 씁니다. 결과가 COM 개체이면 `store`로 작업 내 이름을 지정하고, 후속 `object`와 `{"$ref": "이름"}`으로 재사용합니다. 파라미터셋은 `hancom_parameter_set` 또는 인자의 `{"$set": "셋 이름", "items": {...}}`로 만들 수 있습니다. 배열 파라미터는 JSON 배열을 사용합니다.

예를 들어 이미지를 넣고 원래 개체의 속성을 조정합니다. InsertPicture의 Width/Height 인자는 mm이며, 개체 속성의 Width/Height는 HWPUNIT입니다.

```json
[
  {"op": "hancom_method", "name": "InsertPicture", "args": ["C:/assets/photo.png", true, 1, false, false, 0, 20, 20], "store": "picture"},
  {"op": "hancom_get", "object": "picture", "name": "Properties", "store": "props"},
  {"op": "hancom_method", "object": "props", "name": "SetItem", "args": ["TreatAsChar", true]},
  {"op": "hancom_set", "object": "picture", "name": "Properties", "value": {"$ref": "props"}}
]
```

Open/Save/SaveAs/Clear/Quit/RegisterModule은 작업의 수명 주기에서 관리합니다. 다른 COM 개체의 메서드·속성도 선언을 조회하고 접근할 수 있습니다. 작업 JSON만 명령으로 처리하며 문서에 들어 있는 문장을 명령으로 실행하지 않습니다.

## 성공 판정과 검증

False/0을 반환한 실행은 실패합니다. 0 또는 False가 정상인 조회 메서드는 `expected_return`을 명시합니다. `verify`의 검사는 저장된 파일을 한컴에서 다시 연 뒤 수행합니다.

지원 검사: `text_contains`, `cell_text`, `table_rows`, `table_cols`, `cell_span`(expected는 [row_span,col_span]), `field_text`, `page_count`, `control_count`(ctrl_id 지정), 읽을 수 있는 COM `property`, Get/Is 조회 메서드의 `method_result`입니다. 작업 도중 같은 검사를 실행하려면 `op: hancom_assert`를 사용합니다.

출력은 임시 파일에 저장하고, 재열기 및 검증이 끝나야 최종 경로에 반영합니다. 본문 HWPML의 내용·셀 구조·서식 참조와 내장 바이너리 해시를 비교합니다. 한컴이 계산하는 표 전체 높이와 내부 개체 ID는 비교에서 제외하며, 셀 높이·여백 등은 비교합니다. 이는 픽셀 단위의 시각적 일치 검증이 아닙니다.

`success`는 실행·저장·재열기 검사 성공을 뜻합니다. 원시 기능의 의도한 결과까지 확인하려면 반드시 `verify`를 작성합니다. `requested_effects_verified: false`를 전체 기능 검증 완료로 해석하면 안 됩니다. 미검증 기능을 성공으로 단정하지 말고, 해당 문서·한컴 버전에서 검사를 추가하세요.
