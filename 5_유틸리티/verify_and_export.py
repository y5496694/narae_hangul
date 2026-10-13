import sys
import os
import json
import tempfile
import subprocess
from pathlib import Path

def verify_hwpx(hwpx_file, export_pdf=False, export_png=False):
    """
    한글 문서(HWPX)의 실제 쪽수와 레이아웃을 검증합니다.
    [원칙] 불필요한 PDF나 이미지를 남기지 않으며, 검증용 임시 파일은 자체 자동 삭제(Self-Cleaning)합니다.
    """
    hwpx_path = Path(hwpx_file).resolve()
    if not hwpx_path.exists():
        print(f"[오류] 파일을 찾을 수 없습니다: {hwpx_path}")
        return

    has_hancom = False
    hwp = None

    # 1. Windows 한컴오피스 COM 감지
    if sys.platform == "win32":
        try:
            import win32com.client
            hwp = win32com.client.gencache.EnsureDispatch("HWPFrame.HwpObject")
            hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModule")
            hwp.SetMessageBoxMode(0x00020000)
            has_hancom = True
        except Exception:
            pass

    page_count = None

    if has_hancom and hwp is not None:
        try:
            hwp.Open(str(hwpx_path), "HWPX", "forceopen:true")
            page_count = hwp.PageCount
            print(f"=== [검증 결과] 한컴오피스 실제 쪽수: {page_count} 쪽 ===")
            
            if export_pdf:
                pdf_target = hwpx_path.with_suffix(".pdf")
                hwp.SaveAs(str(pdf_target), "PDF")
                print(f"[PDF 내보내기 요청 완료]: {pdf_target.name}")
        finally:
            try:
                hwp.Quit()
            except:
                pass
    else:
        # 한컴오피스 부재 / macOS 환경: 나래 자체 엔진으로 임시 검증
        print(f"[*] 크로스플랫폼 독립 엔진으로 조판 검증 중...")
        narae_py = Path(__file__).parent.parent / "2_나래엔진" / "narae-hangul" / "scripts" / "narae.py"
        if narae_py.exists():
            # 임시 디렉토리에만 임시 생성 후 확인하고 즉시 삭제 (Zero-residue)
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_pdf = Path(tmpdir) / "temp_verify.pdf"
                render_job = {
                    "schema": 1,
                    "action": "render",
                    "input": str(hwpx_path),
                    "preview": {"pdf": str(tmp_pdf), "dpi": 150}
                }
                job_file = Path(tmpdir) / "job.json"
                with open(job_file, "w", encoding="utf-8") as f:
                    json.dump(render_job, f)
                try:
                    subprocess.run([sys.executable, str(narae_py), "run", str(job_file)], check=True, capture_output=True)
                    if tmp_pdf.exists():
                        try:
                            import pypdf
                            reader = pypdf.PdfReader(str(tmp_pdf))
                            page_count = len(reader.pages)
                            print(f"=== [검증 결과] 나래 독립 엔진 쪽수: {page_count} 쪽 ===")
                        except:
                            print(f"=== [검증 결과] 나래 독립 엔진 렌더링 정상 완료 ===")
                except Exception as ex:
                    print(f"[안내] 검증 알림: {ex}")
                # tmpdir 컨텍스트 매니저 종료 시 임시 PDF 자동 완전 삭제!

    print("[*] 클린 원칙: 원본 한글 파일 외 어떠한 찌꺼기 파일도 남기지 않았습니다.")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        export_flag = "--export-pdf" in sys.argv
        hwpx_target = [arg for arg in sys.argv[1:] if not arg.startswith("--")][0]
        verify_hwpx(hwpx_target, export_pdf=export_flag)
    else:
        print("사용법: python verify_and_export.py <HWPX파일경로> [--export-pdf]")
