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
- 자간은 글자·공백 각각의 폭에 비율로 적용하고, HWP 제어 문자의 UTF16 위치를 유지하되 폭은 더하지 않는다. 기울임 얼굴이 없는 서체는 외곽선·비트맵을 기준선 주위로 약 1/3 기울이고 실제 Italic 얼굴은 유지한다. 글꼴 내부 대체·600 dpi 정수 좌표·폭 누적·PDF 수치 정밀도 차이가 남으므로 스타일 표본 검사만으로 전체 화면이 같다고 판단하지 않는다. 캐시가 없는 문단은 `fontLineHeight`가 꺼져 있으면 선언한 글자 크기로 줄 높이를 계산한다. 표·도형의 `none` 채우기는 투명하게 읽는다.
- 한컴 자체 조판이 필요한 읽기·출력은 `--backend hancom`을 명시한다. Windows, 설치된 한컴, pywin32, 등록된 파일 접근 모듈이 필요하다. 개인 작업 사본을 별도 문서 탭에서 PDF로 출력한다. 이를 독립 엔진의 일치로 보고하지 않는다.
- `read`는 쪽별 텍스트와 용지 크기를 JSON으로 반환한다. `open`은 편집 기능이 없는 창을 연다. PDF는 전체 문서이며 `--pages`는 읽기·PNG 범위에만 적용된다. PNG는 화면과 동일한 PDF에서 흰 종이 배경으로 렌더링한다.
- 종료 코드 `0`은 실행 성공 또는 전체 비교 일치, `1`은 실행 오류, `2`는 비교 완료 후 차이 발견이다. `success:true`만으로 일치라고 판단하지 말고 `comparison.match`, `page_count_match`, 모든 `pages`를 확인한다.
- 비교는 전체 쪽의 크기와 같은 DPI의 RGB 픽셀을 검사한다. 크기 맞춤·좌표 이동·허용 오차를 적용하지 않는다. 다른 쪽이나 크기 불일치도 실패한다. `--reference`에 HWP/HWPX를 주면 기본적으로 양쪽을 독립 렌더링하므로 한컴 호환성을 증명하지 않는다. 한컴과 비교하려면 `--hancom`, 기준 PDF, 또는 `--reference-backend hancom`을 사용한다.
- 같은 문서를 한컴으로 두 번 출력한 일치는 한컴 출력의 재현성이다. 독립 엔진의 일치·모든 문서 지원으로 확대하지 않는다. 기준 PDF의 출처와 DPI, 검사한 문서·쪽수·차이 범위를 함께 보고한다.
- 출력은 사용자가 지정한 경로에만 만든다. 기존 출력 교체는 `--overwrite`가 필요하며 입력·기준 파일과 같은 경로는 거부한다. 검증 목적이면 임시 폴더를 사용한다. 사용자 문서, PDF, 쪽 이미지와 설치된 한컴 바이너리·글꼴을 GitHub에 올리지 않는다.

실행 환경은 저장소의 `1_나래엔진/narae-hangul/scripts/engine/requirements.txt`를 따른다. `narae.py setup`으로 설치할 수 있다. 문서 편집 요청은 기존 나래한글 스킬과 `narae.py run`으로 처리한 뒤 이 뷰어로 결과를 확인한다.
