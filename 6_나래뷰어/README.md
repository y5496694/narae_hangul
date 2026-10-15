# 나래한글 뷰어

HWP/HWPX 읽기 전용 화면·CLI입니다. PDF와 쪽별 PNG로 출력하고 나래엔진의 결과를 한컴 기준 PDF와 전체 쪽 비교할 수 있습니다.

그림의 잘라내기·뒤집기·회색조·흑백·밝기·대비와 PDF 투명도 점무늬를 표시하고 그림을 무손실로 넣습니다. 설치된 HFT의 보통 외곽선과 명조·고딕 비트맵도 읽습니다. 남은 무늬 시작 위치·프린터 좌표·미지원 효과와 글꼴 차이는 검증 JSON의 `layout.image_limitations`, `hft_fonts`, `font_substitutions`, `glyph_substitutions`에서 확인할 수 있습니다.

자간은 각 글자와 공백의 폭에 비율로 적용합니다. 원본 제어 문자의 UTF16 위치는 유지하면서 가짜 자간 폭을 없앴습니다. 기울임 얼굴이 없는 서체에는 기준선 중심의 약 1/3 합성 기울임을 쓰고 실제 Italic 얼굴은 유지합니다. 글자별 대체와 프린터 정수 좌표·누적 폭·PDF 정밀도는 전체 화면 일치에 남은 과제입니다. 캐시 없는 문단의 줄 높이는 `fontLineHeight` 설정을 따르며 표·도형의 `none` 채우기는 투명하게 표시합니다.

![나래한글 뷰어 화면](preview.png)

Windows에서는 `narae_viewer.pyw`를 더블클릭하면 문서 선택창이 열립니다. Python과 엔진 의존성이 필요합니다.

```powershell
python narae_viewer.py open "보고서.hwp"
python narae_viewer.py read "보고서.hwpx" --result read.json
python narae_viewer.py export "보고서.hwp" --pdf view.pdf --png-dir pages
python narae_viewer.py verify "보고서.hwpx" --hancom --result comparison.json
```

기본값은 독립 SourceLayout입니다. 한컴 자체 조판으로 확인할 때는 `--backend hancom`을 사용합니다. 독립 모드에는 표시 차이가 남으므로 **모든 문서의 쪽수·픽셀 일치가 완료된 버전은 아닙니다.**

[명령·설치·비교 결과 안내](../1_나래엔진/narae-hangul/references/viewer.md) · [AI 스킬](../2_나래스킬/narae-viewer/SKILL.md)
