# 나래한글 뷰어

HWP/HWPX 읽기 전용 화면·CLI입니다. PDF와 쪽별 PNG로 출력하고 나래엔진의 결과를 한컴 기준 PDF와 전체 쪽 비교할 수 있습니다.

그림의 잘라내기·뒤집기·회색조·흑백·밝기·대비와 PDF 투명도 점무늬를 표시합니다. 불투명 BMP/PNG/JPEG/GIF의 RGB·팔레트·회색조에는 Windows GDI+ x86 축소, 중간 저장과 자동 JPEG 품질, 프린터의 그림 좌표·클립을 적용합니다. JPEG는 최대 500 dpi, 나머지는 96 dpi이며 확대하지 않습니다. GIF 팔레트·회색조는 유지하고 나머지는 RGB555 색상 점무늬를 씁니다. 같은 픽셀의 첫 JPEG 품질을 재사용하고 계산한 픽셀을 PDF에 추가 손실 없이 넣습니다. 설치된 HFT의 보통 외곽선과 명조·고딕 비트맵도 읽습니다. 남은 무늬 시작 위치·프린터 좌표·미지원 효과와 글꼴 차이는 검증 JSON의 `layout.image_limitations`, `pdf_bitmaps`, `pdf_bitmap_text`, `hft_fonts`, `font_substitutions`, `glyph_substitutions`에서 확인할 수 있습니다.

자간은 각 글자와 공백의 폭에 비율로 적용합니다. 원본 제어 문자의 UTF16 위치는 유지하면서 가짜 자간 폭을 없앴습니다. 기울임 얼굴이 없는 서체에는 기준선 중심의 약 1/3 합성 기울임을 쓰고 실제 Italic 얼굴은 유지합니다. 글자별 대체와 프린터 정수 좌표·누적 폭·PDF 정밀도는 전체 화면 일치에 남은 과제입니다. 캐시 없는 문단의 줄 높이는 `fontLineHeight` 설정을 따르며 표·도형의 `none` 채우기는 투명하게 표시합니다.

명조·고딕의 보통 비트맵은 크기별 저장 글리프·OR 축소·단어 버퍼·누적 정수 프린터 좌표를 사용합니다. HWP/HWPX의 단일 글자 208쪽과 문장 124쪽은 쪽 전체 픽셀이 일치했습니다. 혼합 글꼴·미지원 문자·굵게/기울임·장평·배분 정렬 등은 `layout.pdf_bitmap_text`의 기존 경로 사유를 함께 확인합니다. 실제 7개 문서 124쪽의 전체 무차이는 아직 달성하지 못했습니다.

![나래한글 뷰어 화면](preview.png)

그림 보조 프로그램은 Windows .NET Framework 4의 x86 컴파일러가 있으면 자체 C# 소스를 사용자 로컬 캐시에 빌드합니다. 한컴은 실행하지 않습니다. 환경이 없거나 그림 경로가 지원 범위 밖이면 `layout.pdf_bitmaps`에 원본 경로 사용과 사유를 기록합니다. 원본 알파, 그림 alpha·효과·잘라내기·반전·회전과 다른 그림 형식의 출력 차이는 남아 있습니다. 합성 시험 44쪽의 전체 일치가 실제 모든 문서의 일치를 의미하지는 않습니다.

Windows에서는 `narae_viewer.pyw`를 더블클릭하면 문서 선택창이 열립니다. Python과 엔진 의존성이 필요합니다.

```powershell
python narae_viewer.py open "보고서.hwp"
python narae_viewer.py read "보고서.hwpx" --result read.json
python narae_viewer.py export "보고서.hwp" --pdf view.pdf --png-dir pages
python narae_viewer.py verify "보고서.hwpx" --hancom --result comparison.json
```

기본값은 독립 SourceLayout입니다. 한컴 자체 조판으로 확인할 때는 `--backend hancom`을 사용합니다. 독립 모드에는 표시 차이가 남으므로 **모든 문서의 쪽수·픽셀 일치가 완료된 버전은 아닙니다.**

[명령·설치·비교 결과 안내](../1_나래엔진/narae-hangul/references/viewer.md) · [AI 스킬](../2_나래스킬/narae-viewer/SKILL.md)
