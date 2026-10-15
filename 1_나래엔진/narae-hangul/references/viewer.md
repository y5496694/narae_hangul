# 나래한글 뷰어 CLI

기존 에디터의 `SourceLayout`을 편집 UI와 분리한 읽기 전용 프로그램입니다. HWP/HWPX를 열어 쪽별 내용을 읽고, 화면으로 확인하며 PDF와 PNG로 출력합니다. HWP/HWPX 저장 명령은 제공하지 않습니다.

저장소 루트에서 실행합니다. 엔진 설치는 `python "1_나래엔진/narae-hangul/scripts/narae.py" setup`을 사용합니다.

```powershell
python "6_나래뷰어/narae_viewer.py" read "문서.hwp" --pages 1,3-5 --result read.json
python "6_나래뷰어/narae_viewer.py" open "문서.hwpx"
python "6_나래뷰어/narae_viewer.py" export "문서.hwp" --pdf view.pdf --png-dir pages --dpi 144
python "6_나래뷰어/narae_viewer.py" export "문서.hwpx" --backend hancom --pdf hancom.pdf
python "6_나래뷰어/narae_viewer.py" verify "문서.hwp" --hancom --result comparison.json --diff-dir differences
python "6_나래뷰어/narae_viewer.py" verify "문서.hwpx" --reference hancom.pdf --dpi 96 --result comparison.json
```

동일한 명령을 기존 `1_나래엔진/narae-hangul/scripts/narae.py` 뒤에 붙일 수도 있습니다. 뷰어 명령은 이전 편집기 실행 파일 설정과 관계없이 포함된 소스의 읽기 전용 모듈을 사용합니다. `read --help`처럼 명령별 도움말을 볼 수 있습니다.

## 읽기 엔진

| 값 | 동작 | 검증에서의 의미 |
| --- | --- | --- |
| `independent` (기본) | 문서 안의 배치·글꼴·개체를 SourceLayout으로 직접 그림 | 한컴 없이 사용 가능. 저장된 배치와 구현 범위에 따라 한컴과 차이가 남음 |
| `hancom` | 로컬 한컴으로 개인 사본을 PDF 출력 | 한컴 조판을 사용한 기준 보기. 독립 렌더링의 일치를 뜻하지 않음 |
| PDF 입력 | PDF 그대로 표시·읽기·PNG 출력 | 주어진 PDF에 한정 |

한컴 경로는 Windows, pywin32, 설치된 한컴과 등록된 FilePathCheckDLL이 필요합니다. 기존 한컴 문서를 닫지 않고 작업이 소유한 문서 탭만 사용합니다. 문서 읽기·PDF 출력은 별도 임시 사본에서 실행하며 원본 해시를 다시 검사합니다. COM 작업 제한 시간은 `--timeout`으로 지정합니다(기본 120초). 숨은 파일 접근 확인창이 생길 환경은 오류로 반환합니다.

기준 출력은 저장된 인쇄 범위·모아 찍기·홀짝 인쇄를 이어받지 않고, 전체 논리 페이지를 1쪽씩 출력하도록 `PrintToPDFEx`를 명시합니다. 출력 PDF 쪽수와 한컴 `PageCount`도 대조합니다. 같은 Windows 세션의 나래 한컴 작업은 공유 활성 문서 충돌을 줄이기 위해 직렬 실행합니다(잠금 대기 최대 60초).

## 출력과 비교

- `read`: 쪽수, 선택한 쪽의 텍스트·용지 크기, SHA256, 백엔드와 한계가 담긴 JSON. 보이지 않는 Unicode 텍스트층도 읽습니다. 읽기 제한된 배포용 문서의 텍스트를 우회 추출하지 않습니다.
- `open`: 확대·쪽 이동·찾기·현재 쪽 내용·PDF/PNG 저장이 있는 읽기 전용 창. 내용 편집이나 HWP/HWPX 저장 기능은 없습니다.
- `export`: PDF 전체 문서와 `page-0001.png` 형식의 PNG. `--pages`는 PNG 범위만 선택합니다. 화면과 PNG는 같은 PDF를 사용합니다. PNG는 투명 배경을 흰 종이에 합성합니다.
- `verify`: 모든 쪽을 같은 DPI에서 비교합니다. 쪽수, 물리적 용지 크기, RGB 픽셀을 검사하며 크기 조절·좌표 정렬·허용 오차를 적용하지 않습니다. 결과의 `comparison.match`가 일치 판정입니다. 페이지가 없는 경우도 차이로 기록합니다. `--diff-dir`은 차이가 있는 같은 크기의 쪽에 대해 픽셀 차이 이미지를 저장합니다.

`verify --reference 원본.hwp`는 기본적으로 두 파일의 독립 출력끼리 비교합니다. 한컴 기준 비교는 `--hancom`, 한컴에서 만든 기준 PDF, 또는 `--reference-backend hancom`을 명시합니다. `--backend hancom --hancom`의 일치는 한컴 출력 재현성이며 독립 엔진의 완성을 증명하지 않습니다. 픽셀 차이에는 글꼴·배치뿐 아니라 PDF 이미지 압축·리샘플링·래스터라이저 차이도 포함됩니다.

종료 코드: `0` 성공/전체 일치, `1` 실행 오류, `2` 검사 완료 후 차이 발견. `success:true`는 검사가 수행되었다는 뜻이며 표시 일치와 별개입니다. 결과 JSON은 `--result`로 저장할 수 있습니다. 기존 출력 교체에는 `--overwrite`가 필요하고 입력·기준 파일·결과 JSON의 경로 충돌을 거부합니다.

## 호환성 범위

밝기·대비·그림 투명도와 회색조·흑백 조합은 REA 분석과 한컴 PNG 색상 표본으로 확인했습니다. PDF는 불투명 원본에 300 dpi의 8×8 흰 점무늬를 합성합니다. alpha 0~3은 불투명, 252~255는 생략하며 중간 단계의 무늬는 출력 순서에 따라 이동합니다. 한컴 프로세스의 앞선 출력이 시작 위치에 영향을 주므로 독립 PDF는 재현 가능한 0부터 시작하고 `alpha:native-process-brush-phase`를 남깁니다. `alpha:printer-coordinate-rounding`과 원본 알파·회전 진단도 확인합니다. `verify`는 이 차이를 보정하지 않고 그대로 검사합니다.

불투명 BMP/PNG/JPEG/GIF의 RGB·팔레트·회색조 PDF 경로는 Windows GDI+ x86을 씁니다. JPEG는 최대 500 dpi, 나머지는 96 dpi이며 확대하지 않습니다. 축소 후 JPEG는 GDI+ 기본 JPEG로 중간 저장하고 GIF는 크기를 줄일 때 GDI+ 팔레트로 저장합니다. 원래 크기의 GIF 팔레트와 8비트 회색조는 유지합니다. RGB555의 4×4 색상 양자화에서는 폭이 4n+1인 마지막 열을 세 번째 순위 열로 처리하며, PDF의 동일 픽셀 캐시는 처음 선택한 JPEG 품질을 유지합니다. 그림 위치·크기·클립은 600 dpi 프린터 격자와 PDF 숫자 정밀도로 변환합니다. 색상 점무늬와 투명도 8×8 점무늬는 별개입니다. 자체 C# 소스만 배포하고 한컴 DLL은 사용하지 않습니다. .NET Framework 4 x86 컴파일러가 없거나 원본 알파·그림 alpha·효과·잘라내기·반전·회전 등 지원 밖의 경로는 기존 표시와 사유를 `layout.pdf_bitmaps`에 남깁니다. `prepared`, `jpeg_quality_reused`, 합성 44쪽과 실제 사진 표본의 일치를 다른 문서의 전체 일치로 해석하지 않습니다.

설치된 한양 신명조·중고딕과 HCI Poppy·Hollyhock의 보통 외곽선을 읽습니다. 명조·고딕은 요청한 600 dpi 정수 em 이상인 가장 작은 저장 크기를 선택하고, 없으면 가장 큰 크기를 씁니다. 정수 픽셀 경계로 확대하며 축소할 때 모든 검정 픽셀을 OR 합성해 가는 획을 유지합니다. 보통 문장은 단어·문자 종류별 전체 버퍼로 그리며, 글자 폭을 4 HWPUNIT 격자로 반올림한 뒤 자간을 적용하고 다시 반올림합니다. 기본 반 em 공백은 버림하고 문단 마지막 글자에는 자간을 더하지 않습니다. 논리 위치의 누적 합을 600 dpi로 반올림해 버퍼 사이의 위상을 유지합니다. 기본 FontDB 대체 순서와 미지원 문자 진단도 유지합니다. `hft_fonts`의 `normal_bitmap_only`, `bitmap_renderer`, `font_substitutions`, `glyph_substitutions`, `font_fallback_config`를 확인하세요. 설치 글꼴·문자표·변환 캐시는 배포하지 않습니다. 단일 글자 208쪽과 자간·정렬·색상·2~100pt 문장 124쪽은 쪽 전체가 일치했습니다. `layout.pdf_bitmap_text`는 처리한 줄과 기존 경로 사용 사유를 기록합니다. 혼합 대체 글꼴·스타일·배경·배분 정렬 등 지원 밖의 줄은 기존 경로와 사유를 남깁니다. 처리 수와 이 표본의 일치를 실제 모든 문서의 일치로 확대하지 않습니다.

자간은 각 글자와 공백의 폭에 비율로 적용합니다. 원본 제어 문자의 UTF16 위치는 유지하면서 가짜 자간 폭을 없앴습니다. 기울임 얼굴이 없는 서체에는 기준선 중심의 약 1/3 합성 기울임을 쓰고 실제 Italic 얼굴은 유지합니다. 글자별 대체와 프린터 정수 좌표·누적 폭·PDF 정밀도는 전체 화면 일치에 남은 과제입니다. 캐시 없는 문단의 줄 높이는 `fontLineHeight` 설정을 따르며 표·도형의 `none` 채우기는 투명하게 표시합니다.

독립 모드는 모든 HWP/HWPX의 쪽수·픽셀 일치를 보장하지 않습니다. 특히 저장된 줄 배치가 없는 문단, HFT 비트맵의 위치·스타일·미지원 문자·힌팅, 미지원 개체와 한컴 PDF의 그림 압축에 차이가 남습니다. 한컴 자체로 정확히 확인할 작업은 한컴 백엔드를 사용하고, 독립 모드 개선은 기준 출력과 별도로 비교합니다. 검증 표본과 남은 차이는 `6_나래뷰어/validation.md`를 확인하세요.
