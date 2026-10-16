---
name: narae-viewer
description: 나래한글 뷰어 CLI로 HWP/HWPX 문서를 읽고 쪽수·내용을 확인하며 화면, PDF, 쪽별 PNG로 출력하고 한컴 기준 또는 다른 문서와 전체 쪽을 비교한다. 문서 편집에는 narae-hangul을 사용한다.
---

# 나래한글 뷰어

읽기와 검증에는 이 스킬의 `scripts/viewer.py`를 실제 실행한다. 원본 HWP/HWPX를 저장하거나 편집하지 않는다. 저장소 전체가 필요하며 단독 설치 시 `NARAE_HANGUL_ROOT` 또는 스킬의 `runtime.json`에 `repository` 경로를 지정한다.

```powershell
python <skill-dir>/scripts/viewer.py read "문서.hwp" --pages 1-3 --result read.json
python <skill-dir>/scripts/viewer.py open "문서.hwpx"
python <skill-dir>/scripts/viewer.py export "문서.hwpx" --pdf view.pdf --png-dir pages --dpi 144
python <skill-dir>/scripts/viewer.py verify "문서.hwp" --hancom --result comparison.json --diff-dir differences
python <skill-dir>/scripts/viewer.py verify "원본.hwp" --reference "수정본.hwpx" --result comparison.json
```

- 기본 `independent`는 기존 나래 에디터의 `SourceLayout` 읽기 모드다. 한컴을 실행하지 않는다. 저장된 배치가 없거나 미지원 글꼴·개체가 있으면 표시와 쪽수가 다를 수 있다. `warnings`와 `layout`을 확인한다.
- 그림은 잘라내기·뒤집기·회색조·흑백·밝기·대비·투명도를 표시한다. 래스터 경로는 원본 알파에서 그림 alpha를 빼고, 뷰어의 PDF 경로는 불투명 원본에 한컴의 300 dpi 8×8 흰 점무늬를 합성한다. PDF의 alpha 0~3은 불투명, 252~255는 생략하는 동작도 따른다. 한컴의 무늬 시작 위치는 앞선 출력에 영향을 받으므로 `alpha:native-process-brush-phase`, 좌표 반올림은 `alpha:printer-coordinate-rounding`으로 진단한다. 원본 알파·회전과 미지원 효과도 `layout.image_limitations`를 확인한다. 무늬 바이트나 내부 색상 표본의 일치를 쪽 전체 일치로 확대하지 않는다.
- 불투명 BMP/PNG/JPEG/GIF의 RGB·팔레트·회색조 PDF 경로는 Windows GDI+ x86 축소와 프린터 그림 좌표·클립을 적용한다. JPEG는 최대 500 dpi, 나머지는 96 dpi이며 확대하지 않는다. JPEG/GIF의 중간 저장, GIF 팔레트·8비트 회색조 유지, RGB555 4×4 색상 점무늬의 마지막 홀수 열, 같은 픽셀의 첫 JPEG 품질 재사용도 반영한다. 원본 알파·그림 alpha·효과·잘라내기·반전·회전과 다른 그림 형식은 기존 경로로 남을 수 있으므로 `layout.pdf_bitmaps`의 `status`, `remaining`, `whole_page_parity_verified`를 읽는다. `prepared`는 처리가 실행됐다는 뜻이다. 합성 시험 44쪽과 실제 사진 8개 표본의 일치를 다른 문서 전체의 일치로 확대하지 않는다. 보조 프로그램은 한컴을 쓰지 않고 .NET Framework 4의 x86 컴파일러로 자체 C# 소스를 로컬 캐시에 빌드한다. 환경이 없으면 기존 경로와 사유를 남긴다.
- 글꼴 확인에는 `layout.font_substitutions`, `glyph_substitutions`, `hft_fonts`, `font_fallback_config`도 읽는다. 설치된 한양 신명조·중고딕의 보통 외곽선과 HCI Poppy·Hollyhock 영문, 명조·고딕의 보통 비트맵을 지원한다. 비트맵은 요청 크기에 맞는 저장 크기를 고르고 600 dpi 정수 경계 확대·OR 축소로 가는 획을 유지한다. 보통 문장은 문자 종류·단어별 버퍼와 4 HWPUNIT 논리 간격, 누적 정수 프린터 좌표로 PDF에 넣는다. 명조·고딕의 단일 글자 208쪽과 문장 124쪽은 전체 쪽이 일치했지만, 다른 글꼴·스타일·문서의 동일성 검증은 아니다. `layout.pdf_bitmap_text`의 `processed_lines`, `legacy_lines`, `legacy_reasons`를 함께 읽는다. `processed_lines`는 처리 수이며 전체 일치 판정이 아니다. 미지원 문자, 굵게/기울임·힌팅·프린터 좌표 차이가 남고 설치 글꼴이나 변환 캐시는 배포하지 않는다.
- 보통 글꼴의 PDF에는 설치된 원본 TTF/TTC의 힌팅·글리프 경계를 보존하고, HFT 외곽선과 누락된 한글의 휴먼명조·고딕 조합 외곽선을 직접 넣는다. 한자 대체는 설치 FontDB의 Hanja 순서를 읽는다. `layout.pdf_native_text`의 `fonts`, `glyph_fallbacks`, `legacy_lines`, `legacy_reasons`를 확인한다. HWP/HWPX 각각 101쪽의 글꼴·크기·색·자간·정렬·혼합 글꼴·긴 문단 표본이 전체 일치했지만 다른 문서의 동일성은 아니다. 장평은 TTF/TTC의 정수 평균 폭과 실제 PDF 가로 배율, HFT의 요청 비율을 구분하고 공백의 기본 폭을 먼저 맞춘다. 7개 서체·5/11/13/18pt·장평 50/95/105/150%, 자간·정렬·글꼴 공백 조합은 두 형식 총 352쪽이 96 dpi에서 전체 일치했다. `stretched_glyphs`는 처리 수다. 끝 공백은 정렬 폭에서 제외하지만 함초롬바탕의 끝 공백이 있는 오른쪽 정렬 2개 추가 표본은 1프린터 픽셀 수준의 폭 차이가 남았다. 다른 DPI·문자 조합·스타일의 일치로 확대하지 않는다. 굵게/기울임·배분 정렬·문자 배경 등은 기존 경로와 사유를 남길 수 있으며 처리 수를 전체 일치로 해석하지 않는다.
- 자간은 글자·공백 각각의 폭에 비율로 적용하고, HWP 제어 문자의 UTF16 위치를 유지하되 폭은 더하지 않는다. 예전 HWP의 가운데선은 취소선으로 읽고 밑줄 필드의 원래 모양·색을 유지한다. HWP/HWPX의 취소선 모양·색을 보존하며 `THICK3D`·`THICKREV3D`·`3D`는 한컴처럼 선을 그리지 않는다. 이 세 모양의 6개 서체·2개 크기·두 형식 72쪽은 전체 일치했으며 `layout.pdf_native_text.suppressed_strike_glyphs`는 처리 수만 나타낸다. 실선·물결 등 나머지 취소선의 위치·무늬와 위 밑줄 차이는 남을 수 있다. 기울임 얼굴이 없는 서체는 외곽선·비트맵을 기준선 주위로 약 1/3 기울이고 실제 Italic 얼굴은 유지한다. 미지원 스타일과 개체 차이가 남으므로 스타일 표본 검사만으로 전체 화면이 같다고 판단하지 않는다. 캐시가 없는 문단은 논리 글자 폭으로 줄을 나누고, `fontLineHeight`가 꺼져 있으면 선언한 글자 크기로 줄 높이를 계산한다. 글자처럼 배치한 표·도형 뒤에는 문단 간격을 유지하고, `affectLineSpacing`이 켜진 백분율 간격은 커진 개체 높이를 기준으로 계산한다. 단순 HWPX 문단 여백은 100단위/pt, 버전별 default는 200단위/pt로 읽어 구분하며 원본 보존 편집도 같은 단위를 따른다. 표·도형의 `none` 채우기는 투명하게 읽는다.
- 표 PDF는 본문에서 글자처럼 배치한 한 쪽짜리 병합 셀을 포함한 완전 직사각형 격자의 셀 테두리가 SOLID/NONE일 때, 누적 원점의 프린터 좌표·공유 선·홀수 두께의 좌우 연장·셀 배경과 테두리 순서를 반영한다. Arial 13pt의 행·열·정렬·채우기·두께 조합은 HWP/HWPX 총 88쪽, 채운 셀과 다음 쪽의 정밀도 표본은 4쪽이 96 dpi·오차 0으로 전체 일치했다. `layout.pdf_table_frames`의 `processed_grids`, `legacy_grids`, `legacy_reasons`를 읽으며 처리 수를 전체 일치로 해석하지 않는다. 쪽 나눔·떠 있는 표·중첩 표·다른 선 모양은 기존 경로에 남는다. HWPX borderFill 두께는 정확한 16개 문자열 열거형이며 비표준 표기는 한컴처럼 기본 0.1 mm로 읽는다. 저장은 두 형식 모두 가장 가까운 표준 두께를 쓴다. 실제 파일 전체 무차이는 아직 미달성이므로 전체 쪽 비교 결과를 확인한다.
- 한컴 자체 조판이 필요한 읽기·출력은 `--backend hancom`을 명시한다. Windows, 설치된 한컴, pywin32, 등록된 파일 접근 모듈이 필요하다. 개인 작업 사본을 별도 문서 탭에서 PDF로 출력한다. 이를 독립 엔진의 일치로 보고하지 않는다.
- `read`는 쪽별 텍스트와 용지 크기를 JSON으로 반환한다. `open`은 편집 기능이 없는 창을 연다. PDF는 전체 문서이며 `--pages`는 읽기·PNG 범위에만 적용된다. PNG는 화면과 동일한 PDF에서 흰 종이 배경으로 렌더링한다.
- 종료 코드 `0`은 실행 성공 또는 전체 비교 일치, `1`은 실행 오류, `2`는 비교 완료 후 차이 발견이다. `success:true`만으로 일치라고 판단하지 말고 `comparison.match`, `page_count_match`, 모든 `pages`를 확인한다.
- 비교는 전체 쪽의 크기와 같은 DPI의 RGB 픽셀을 검사한다. 크기 맞춤·좌표 이동·허용 오차를 적용하지 않는다. 다른 쪽이나 크기 불일치도 실패한다. `--reference`에 HWP/HWPX를 주면 기본적으로 양쪽을 독립 렌더링하므로 한컴 호환성을 증명하지 않는다. 한컴과 비교하려면 `--hancom`, 기준 PDF, 또는 `--reference-backend hancom`을 사용한다.
- 같은 문서를 한컴으로 두 번 출력한 일치는 한컴 출력의 재현성이다. 독립 엔진의 일치·모든 문서 지원으로 확대하지 않는다. 기준 PDF의 출처와 DPI, 검사한 문서·쪽수·차이 범위를 함께 보고한다.
- 출력은 사용자가 지정한 경로에만 만든다. 기존 출력 교체는 `--overwrite`가 필요하며 입력·기준 파일과 같은 경로는 거부한다. 검증 목적이면 임시 폴더를 사용한다. 사용자 문서, PDF, 쪽 이미지와 설치된 한컴 바이너리·글꼴을 GitHub에 올리지 않는다.

캐시된 한 줄·동일 보통 TTF·장평 100%·자간 0의 PDF 글자는 Windows GDI 장치 폭과 설치 글꼴 원본 표를 대조한 뒤 단어·문자 종류별 폭 보정을 적용한다. `layout.pdf_native_text.device_centered_lines`와 `device_metric_fallbacks`를 확인한다. 소수점 글자 크기는 논리 크기를 4 HWPUNIT 단위로 먼저 버린 뒤 폭을 계산한다. 캐시 표 88쪽과 3개 글꼴·3개 크기·정렬·공백을 섞은 2쪽, 병합 격자 40쪽이 두 형식 전체 비교에서 추가로 일치했다. 숨은 표 경계는 HWP/HWPX 저장 시 같은 보이는 격자로 정리하고 원본 모델의 병합 정보는 유지한다. 여러 줄·혼합 측정값·장평·자간·복잡한 스타일은 이 보정 범위 밖이며 처리 수를 전체 화면 일치로 해석하지 않는다. 실제 원본은 아직 0/7문서·5/124쪽 일치다.

혼합 SOLID/NONE 셀 테두리의 색·두께·그리는 순서·병합 경계·가로선 끝과 바깥선 반복도 두 형식 80쪽 전체 비교에서 일치했다. 점선·이중선 등은 기존 경로와 사유를 유지한다. 한컴 기준 출력은 버전·쪽수 조회를 인쇄 뒤에 수행하며 `hancom_metadata_after_export`를 기록한다. 이 순서의 표본 재현성을 모든 한컴 캐시 상태의 동일성으로 해석하지 않는다.

실행 환경은 저장소의 `1_나래엔진/narae-hangul/scripts/engine/requirements.txt`를 따른다. `narae.py setup`으로 설치할 수 있다. 문서 편집 요청은 기존 나래한글 스킬과 `narae.py run`으로 처리한 뒤 이 뷰어로 결과를 확인한다.
