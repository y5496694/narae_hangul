# 한컴 REA 분석과 검증 범위 · 2026-10-15

대상은 설치된 한컴 2024 `13, 0, 0, 711`의 HwpAutomation.dll 및 COM 런타임이다. REA 6.5.0을 실제 실행했고, Ghidra 12.1.4의 x86 PE 분석과 REA Frida 세션으로 확인했다. DLL이나 한컴 코드를 재배포하지 않는다.

## 재현 가능한 증거

- 대상 DLL SHA-256: `20b6e320542bfc517963b3351a8a5f250ecd5411b3aeb969cf083805e6daa05d`
- Ghidra 분석: 함수 5,426개, 정의된 문자열 1,263개. 이는 한컴의 기능 수나 함수 전체의 의미를 이해한 수가 아니다.
- FilePathCheckDLL 문자열 주소: `0x1009da64`, 참조: `0x10033ad9`, `0x10033ae3`.
- 등록 분기 함수: `FUN_10033a90`, 모듈 로더: `FUN_100025f0`.
- 로더에서 현재 사용자 `Software/HNC/HwpAutomation/Modules`의 값 이름으로 DLL 경로를 읽고, 선택적 `Uses` 값을 확인한 뒤 LoadLibraryW 및 IsAccessiblePath를 사용하는 흐름을 관찰했다.
- REA Frida 관찰에서 레지스트리 키 열기는 성공했지만 값 조회가 오류 2(찾을 수 없음)를 반환했다. 실행 환경에서 보이는 레지스트리 값과 한컴 프로세스의 조회 결과가 달랐다. 별도 시험 값으로 공식 모듈을 등록한 후 RegisterModule 성공과 파일 열기를 확인했다. 기존 사용자 값과 Uses 설정을 변경하지 않았다.

## 이슈 #1과 함께 고친 문제

1. IHwpObject의 메서드 목록에는 InsertText가 없다. InsertText 액션의 파라미터셋을 만들어 Execute해야 한다.
2. 표의 실제 제어 개체 앵커로 이동하고 SelectCtrlReverse/ShapeObjTableSelCell로 진입해야 한다. 문서 시작에서 셀 이동만 실행하면 표 번호를 지정할 수 없다.
3. 병합은 실제 셀 블록을 확장해야 한다. 행 삽입은 TableInsertUpperRow/TableInsertLowerRow를 사용하며, 구조가 바뀌었는지 검사해야 한다.
4. SetItem은 모르는 항목도 받아들일 수 있다. ParagraphShape의 실제 항목은 ParaShape.AlignType이며 Align은 무시되었다. 선언 및 기본값을 검사해 모르는 이름을 실행 전에 거부한다.
5. CellShape.Item("Cell")을 수정한 뒤에는 부모 셋에 다시 넣어야 변경이 반영된다. 세로 정렬·여백이 조용히 적용되지 않던 문제를 수정했다.
6. InsertText는 LF만 주면 줄바꿈을 버렸다. 작업의 LF를 CRLF로 변환한다.
7. 일반 텍스트/서식 편집과 고급 표 편집을 같은 작업에 넣으면 일반 편집이 버려지던 경로를 수정했다.
8. 자연어 HWP 편집이 원본을 축소 모델로 재생성하던 경로는 검사되는 한컴 저장 경로로 바꿨다. 표·누름틀·지원 밖의 개체가 유실될 수 있는 재생성을 원본 편집의 자동 대안으로 사용하지 않는다.
9. DispatchEx가 기존 한컴 프로세스를 재사용할 수 있어 별도 문서 탭과 안정된 문서 ID로 작업 대상을 관리한다. 종료 시 작업 문서만 닫으며 응용 프로그램 전체 Quit을 호출하지 않는다. 중첩 COM 초기화와 해제를 짝지어 다른 자동화 연결을 유지한다.
10. 한컴 2024의 재사용된 컨트롤러에서 저장 직후 Clear가 서버 예외를 내는 사례를 재현했다. 고유한 파일 사본을 열어 디스크 내용을 검증한 다음 저장 대상 파일을 다시 연다.

## 확장 인터페이스

`hancom_capabilities`는 설치된 COM 선언을 조회한다. 해당 런타임에서 선언 항목 214개와 파라미터셋 212개를 관찰했다. 한컴 공식 ActionTable_2504에서 후보 식별자를 추출하고 CreateAction으로 조사해 849개를 카탈로그에 넣었다. 카탈로그는 실행 효과 검증 목록이 아니다.

`hancom` 작업은 원시 액션, 메서드, 속성, 파라미터 배열·중첩 셋, 반환 COM 개체 참조를 지원한다. 새 기능은 설치된 런타임의 선언과 현재 문맥을 조회하고 작업에 사용할 수 있다. 의도한 효과는 verify에 명시한다.

## 검증과 한계

회귀 테스트 21개가 통과했다. 이 가운데 16개는 설치된 한컴을 실제 호출하며, 5개는 실패 판정·HWPX 혼합 편집·출력 보존·입력 검증을 검사한다. 테스트 그룹 안에는 세 표 지정과 여러 삽입 위치 등 추가 하위 사례가 있다. 별도로 CLI의 doctor --hancom에서 엔진 0.6.0과 한컴 연결을 확인했다.

실제 한컴에서 회의록 양식을 생성하고 HWP로 저장해 표 편집을 검사했다. 세 표의 정확한 지정, 빈 셀·추가·여러 줄 입력, 텍스트 유무별 병합, 사각형 병합, 행 삽입·삭제, 단일 행 및 2×2 분할, 글자·문단·셀 서식, 표 속성, 혼합 편집, 누름틀과 중첩 파라미터 작업을 검사한다. 그림의 COM 개체 참조·크기 변경·바이너리 보존과 수식 생성도 검사한다. 원본 해시 및 관련 없는 표의 내용 보존도 검사한다.

두 자동화 세션에서 같은 파일을 열고 별도 내용을 편집한 뒤, 어느 세션을 먼저 닫아도 나머지 문서의 내용과 COM 연결이 유지되는지 추가로 검사했다. 한컴의 응용 프로그램 프로세스는 작업 종료 후 남을 수 있다. 외부 프로그램이나 사용자가 동시에 캐럿을 조작하는 경우까지 원자적으로 격리된다는 뜻은 아니며, 한컴 작업은 순차 실행한다.

실패한 액션·없는 파라미터·잘못된 인덱스가 성공으로 보고되지 않는지, 기존 출력이 실패 시 유지되는지 확인한다. 저장 후 한컴 재열기의 본문 HWPML, 서식 참조 및 내장 데이터 해시를 비교한다. 계산되는 표 전체 높이와 내부 개체 ID는 비교에서 제외한다.

전체 기능을 각각 실행한 검증, 모든 문서의 보존, 독립 엔진과 한컴의 전체 조판 일치, 픽셀 일치는 완료되지 않았다. 버전·문맥·문서 보호·대화상자·외부 연동에 의존하는 액션은 해당 환경에서 추가 검사가 필요하다. REA 분석을 수행했다는 사실만으로 기존 한컴의 모든 기능을 복제했다고 주장하지 않는다.

## 실행

```powershell
python -m unittest discover -s tests -v
$env:NARAE_RUN_HANCOM_TESTS = "1"
# 별도 승인된 모듈 값 이름을 사용한다면:
$env:NARAE_HANCOM_SECURITY_MODULE = "등록된_값_이름"
python -m unittest discover -s tests -v
```

원시 분석 예:

```powershell
$env:GHIDRA_INSTALL_DIR = "C:/tools/ghidra_12.1.4_PUBLIC"
npx -y rea-agents@6.5.0 analyze "C:/Program Files (x86)/HNC/Office 2024/HOffice130/Bin/HwpAutomation.dll" --provider ghidra --json
```

참고: [REA](https://github.com/morluto/rea), [Windows Ghidra 경계](https://github.com/morluto/rea/blob/main/docs/windows-ghidra-p0.md), [한컴 공식 자동화 가이드](https://developer.hancom.com/hwpautomation), [공식 액션 표](https://github.com/hancom-io/devcenter-archive/blob/main/hwp-automation/ActionTable_2504.pdf), [공식 파일 접근 모듈 설명](https://forum.developer.hancom.com/t/topic/3206/2).
