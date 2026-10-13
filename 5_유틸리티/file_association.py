"""NaraePack File Association Tool (HWP / HWPX -> NaraeEditor)"""
import sys
import os
import winreg
from pathlib import Path

def get_editor_exe():
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve())
    cur = Path(__file__).resolve()
    for parent in [cur.parent, cur.parent.parent, cur.parent.parent.parent, cur.parent.parent.parent.parent]:
        cand1 = parent / "1_나래에디터" / "hangeul-editor" / "app" / "NaraeEditor" / "NaraeEditor.exe"
        if cand1.exists():
            return str(cand1.resolve())
        cand2 = parent / "app" / "NaraeEditor" / "NaraeEditor.exe"
        if cand2.exists():
            return str(cand2.resolve())
    return None

def register_association():
    exe_path = get_editor_exe()
    if not exe_path:
        print("[!] 오류: NaraeEditor.exe 실행 파일을 찾을 수 없습니다.")
        return False

    print(f"[*] 나래 에디터 위치: {exe_path}")
    print("[*] HWP 및 HWPX 기본 연결 프로그램을 등록합니다...")

    hkcu = winreg.HKEY_CURRENT_USER

    # 1. Applications/NaraeEditor.exe
    app_key_path = r"Software\Classes\Applications\NaraeEditor.exe"
    with winreg.CreateKey(hkcu, app_key_path) as k:
        winreg.SetValue(k, "", winreg.REG_SZ, "나래 한글 에디터")
    with winreg.CreateKey(hkcu, app_key_path + r"\DefaultIcon") as k:
        winreg.SetValue(k, "", winreg.REG_SZ, f'"{exe_path}",0')
    with winreg.CreateKey(hkcu, app_key_path + r"\shell\open\command") as k:
        winreg.SetValue(k, "", winreg.REG_SZ, f'"{exe_path}" "%1"')
    with winreg.CreateKey(hkcu, app_key_path + r"\SupportedTypes") as k:
        winreg.SetValueEx(k, ".hwp", 0, winreg.REG_SZ, "")
        winreg.SetValueEx(k, ".hwpx", 0, winreg.REG_SZ, "")

    # 2. ProgID: Narae.Hwp, Narae.Hwpx
    progs = [
        ("Narae.Hwp", "한글 문서 (나래 에디터)", ".hwp"),
        ("Narae.Hwpx", "한글 표준 문서 (나래 에디터)", ".hwpx"),
    ]
    for prog_id, desc, ext in progs:
        with winreg.CreateKey(hkcu, rf"Software\Classes\{prog_id}") as k:
            winreg.SetValue(k, "", winreg.REG_SZ, desc)
        with winreg.CreateKey(hkcu, rf"Software\Classes\{prog_id}\DefaultIcon") as k:
            winreg.SetValue(k, "", winreg.REG_SZ, f'"{exe_path}",0')
        with winreg.CreateKey(hkcu, rf"Software\Classes\{prog_id}\shell\open\command") as k:
            winreg.SetValue(k, "", winreg.REG_SZ, f'"{exe_path}" "%1"')

        # 확장자 연결
        with winreg.CreateKey(hkcu, rf"Software\Classes\{ext}") as k:
            winreg.SetValue(k, "", winreg.REG_SZ, prog_id)
        with winreg.CreateKey(hkcu, rf"Software\Classes\{ext}\OpenWithProgids") as k:
            winreg.SetValueEx(k, prog_id, 0, winreg.REG_NONE, b"")
        with winreg.CreateKey(hkcu, rf"Software\Classes\{ext}\OpenWithList\NaraeEditor.exe") as k:
            pass

        # 3. 우클릭 메뉴: "나래 한글로 열기"
        ctx_path = rf"Software\Classes\SystemFileAssociations\{ext}\shell\OpenWithNarae"
        with winreg.CreateKey(hkcu, ctx_path) as k:
            winreg.SetValue(k, "", winreg.REG_SZ, "나래 한글로 열기")
        with winreg.CreateKey(hkcu, ctx_path + r"\command") as k:
            winreg.SetValue(k, "", winreg.REG_SZ, f'"{exe_path}" "%1"')

    # 4. Notify Windows Shell of association change
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None) # SHCNE_ASSOCCHANGED
    except Exception:
        pass

    print("[+] 등록 완료! HWP/HWPX 파일이 나래 한글 에디터에 연결되었습니다.")
    return True

def unregister_association():
    print("[*] 나래 한글 연결 설정을 해제합니다...")
    hkcu = winreg.HKEY_CURRENT_USER

    keys_to_delete = [
        r"Software\Classes\Applications\NaraeEditor.exe",
        r"Software\Classes\Narae.Hwp",
        r"Software\Classes\Narae.Hwpx",
        r"Software\Classes\.hwp",
        r"Software\Classes\.hwpx",
        r"Software\Classes\SystemFileAssociations\.hwp\shell\OpenWithNarae",
        r"Software\Classes\SystemFileAssociations\.hwpx\shell\OpenWithNarae",
    ]

    def delete_key_recursive(root, subkey):
        try:
            with winreg.OpenKey(root, subkey, 0, winreg.KEY_ALL_ACCESS) as k:
                while True:
                    try:
                        child = winreg.EnumKey(k, 0)
                        delete_key_recursive(root, f"{subkey}\\{child}")
                    except OSError:
                        break
            winreg.DeleteKey(root, subkey)
        except OSError:
            pass

    for key in keys_to_delete:
        delete_key_recursive(hkcu, key)

    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
    except Exception:
        pass

    print("[+] 해제 완료! 기본 프로그램 설정이 원래대로 복원되었습니다.")
    return True

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--unregister":
        unregister_association()
    else:
        register_association()
