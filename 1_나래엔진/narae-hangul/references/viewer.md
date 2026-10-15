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

독립 모드는 모든 HWP/HWPX의 쪽수·픽셀 일치를 보장하지 않습니다. 특히 저장된 줄 배치가 없는 문단, HFT 비트맵·미지원 문자·힌팅, 미지원 개체와 한컴 PDF의 그림 압축에 차이가 남습니다. 한컴 자체로 정확히 확인할 작업은 한컴 백엔드를 사용하고, 독립 모드 개선은 기준 출력과 별도로 비교합니다. 검증 표본과 남은 차이는 `6_나래뷰어/validation.md`를 확인하세요.
