#!/usr/bin/env python3
"""Run native Narae Hangul jobs using a packaged app or the bundled engine."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv

SKILL = Path(__file__).resolve().parents[1]
ENGINE = SKILL / "scripts" / "engine"
VERSION = "0.6.0"
MODULES = ("PySide6", "olefile", "fontTools", "Crypto", "pypdf", "PIL")


def cache_python() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".cache")))
    root = base / "NaraeHangul" / "skill-runtime" / VERSION
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def source_command(python: Path) -> tuple[list[str], dict[str, str], str]:
    if not python.is_file():
        raise ValueError(f"Python 실행 파일이 없습니다: {python}")
    probe = subprocess.run(
        [str(python), "-c", "import PySide6,olefile,fontTools,Crypto,pypdf,PIL"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if probe.returncode:
        raise ValueError("이 Python에 엔진 의존성이 없습니다. narae.py setup을 실행하세요.")
    if not (ENGINE / "hangeul" / "automation.py").is_file():
        raise ValueError("스킬에 포함된 엔진이 없습니다. 전체 스킬 폴더를 복사하세요.")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ENGINE)
    env["PYTHONUTF8"] = "1"
    return [str(python), "-m", "hangeul.automation"], env, "bundled-source"


def choose_runtime(args) -> tuple[list[str], dict[str, str], str]:
    if args.engine:
        executable = Path(args.engine).expanduser().resolve()
        if not executable.is_file():
            raise ValueError(f"나래 한글 실행 파일이 없습니다: {executable}")
        return [str(executable)], os.environ.copy(), "explicit-app"
    if args.python:
        return source_command(Path(args.python).expanduser().resolve())
    configured = os.environ.get("NARAE_HANGUL_EXE")
    config_file = SKILL / "runtime.json"
    if not configured and config_file.is_file():
        config = json.loads(config_file.read_text(encoding="utf-8-sig"))
        configured = config.get("executable")
        if configured and not Path(configured).is_absolute():
            configured = str((SKILL / configured).resolve())
    if configured:
        executable = Path(configured).expanduser().resolve()
        if executable.is_file():
            return [str(executable)], os.environ.copy(), "configured-app"
    # The release ships hangeul-editor/ai/skill/narae-hangul next to app/.
    for parent in SKILL.parents:
        executable = parent / "app" / "NaraeHangul" / "NaraeHangul.exe"
        if executable.is_file():
            return [str(executable)], os.environ.copy(), "release-app"
    python = cache_python()
    if python.is_file():
        return source_command(python)
    if all(importlib.util.find_spec(name) is not None for name in MODULES):
        return source_command(Path(sys.executable).resolve())
    raise ValueError("나래 한글 실행 경로를 --engine으로 지정하거나 narae.py setup을 실행하세요.")


def install_hancom_module(module_path: str) -> dict:
    """Register the explicitly supplied module; never download/replace a DLL implicitly."""
    if os.name != "nt": raise ValueError("한컴 COM 모듈은 Windows에서만 등록할 수 있습니다.")
    import winreg
    import shutil
    source = Path(module_path).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() != ".dll": raise ValueError("승인된 한컴 FilePathCheckDLL 파일 경로를 지정하세요.")
    target = cache_python().parent.parent / "FilePathCheckerModule.dll"
    target.parent.mkdir(parents=True, exist_ok=True)
    if source != target: shutil.copy2(source, target)
    name = "NaraeHangulFilePathChecker"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\HNC\HwpAutomation\Modules") as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(target))
    return {"module_name": name, "module_path": str(target)}


def setup(module_path=None) -> dict:
    python = cache_python()
    requirements = ENGINE / "requirements.txt"
    if not requirements.is_file():
        raise ValueError("엔진 requirements.txt가 없습니다.")
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(python.parent.parent)
    process = subprocess.run(
        [str(python), "-m", "pip", "install", "-r", str(requirements)],
        stdout=sys.stderr, stderr=sys.stderr,
    )
    if process.returncode:
        raise ValueError("독립 엔진 의존성 설치가 실패했습니다. pip 오류를 확인하세요.")
    source_command(python)
    response = {"success": True, "python": str(python), "version": VERSION}
    if module_path: response["hancom_module"] = install_hancom_module(module_path)
    return response


def write_result(path: Path | None, response: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".json", delete=False) as stream:
        staged = Path(stream.name)
        stream.write(json.dumps(response, ensure_ascii=False, indent=2).encode("utf-8"))
    try:
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def run(args) -> tuple[int, dict]:
    job = Path(args.job).expanduser().resolve()
    if not job.is_file():
        raise ValueError(f"작업 JSON이 없습니다: {job}")
    # Read only task JSON, never evaluate document text as Python or shell code.
    task = json.loads(job.read_text(encoding="utf-8-sig"))
    if not isinstance(task, dict):
        raise ValueError("작업 JSON은 객체여야 합니다.")
    command, env, runtime = choose_runtime(args)
    explicit_result = Path(args.result).expanduser().resolve() if args.result else None
    paths = [job]
    preview = task.get("preview") or {}
    for value in [task.get("input"), task.get("output"),
                  *(preview.get(key) for key in ("png", "pdf") if isinstance(preview, dict))]:
        if isinstance(value, str) and value:
            candidate = Path(value).expanduser()
            paths.append((job.parent / candidate).resolve() if not candidate.is_absolute() else candidate.resolve())
    if explicit_result in paths:
        raise ValueError("결과 JSON 경로는 작업 JSON·문서·미리보기 경로와 달라야 합니다.")
    with tempfile.TemporaryDirectory(prefix="narae-job-") as temporary:
        # Always use a new result path: a previous successful JSON must never
        # disguise an engine crash or failure to write this job's result.
        result = Path(temporary) / "result.json"
        process = subprocess.run(
            command + ["--automation", str(job), "--result", str(result)],
            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if not result.is_file():
            response = {"success": False, "runtime": runtime,
                        "error": "엔진이 결과 JSON을 반환하지 않았습니다.",
                        "exit_code": process.returncode,
                        "diagnostic": (process.stderr or process.stdout)[-4000:]}
            write_result(explicit_result, response)
            return 1, response
        response = json.loads(result.read_text(encoding="utf-8-sig"))
        response["skill_runtime"] = runtime
        write_result(explicit_result, response)
        return (0 if process.returncode == 0 and response.get("success") else 1), response


def run_viewer(argv) -> int:
    """Use source viewer code with current dependencies or the setup runtime."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ENGINE) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUTF8"] = "1"
    if all(importlib.util.find_spec(name) is not None for name in MODULES):
        sys.path.insert(0, str(ENGINE))
        os.environ["PYTHONPATH"] = env["PYTHONPATH"]
        from hangeul.viewer import main as viewer_main
        return viewer_main(argv)
    python = cache_python()
    if python.is_file() and python.resolve() != Path(sys.executable).resolve():
        return subprocess.run([str(python), "-m", "hangeul.viewer", *argv], env=env).returncode
    print(json.dumps({"success": False,
        "error": "뷰어 의존성이 없습니다. narae.py setup을 실행하거나 engine/requirements.txt를 설치하세요."}, ensure_ascii=False))
    return 1


def main() -> int:
    # Viewer commands always use the bundled read-only module, even when an
    # older editor executable is configured for document editing jobs.
    if len(sys.argv) > 1 and sys.argv[1] in ("read", "open", "export", "verify"):
        return run_viewer(sys.argv[1:])
    parser = argparse.ArgumentParser(description="나래 한글: AI용 HWP 작업 실행")
    parser.add_argument("action", choices=("run", "doctor", "setup"))
    parser.add_argument("job", nargs="?")
    parser.add_argument("--result", help="엔진 결과 JSON 저장 경로")
    parser.add_argument("--engine", help="NaraeHangul.exe 경로")
    parser.add_argument("--python", help="포함된 소스 엔진에 사용할 Python 경로")
    parser.add_argument("--hancom", action="store_true", help="doctor에서 한컴 COM/파일 접근 모듈도 확인")
    parser.add_argument("--hancom-module", help="setup에서 명시적으로 지정한 승인된 FilePathCheckDLL 등록")
    args = parser.parse_args()
    if args.hancom and args.action != "doctor": parser.error("--hancom은 doctor에서만 사용합니다.")
    if args.hancom_module and args.action != "setup": parser.error("--hancom-module은 setup에서만 사용합니다.")
    try:
        if args.action == "run":
            if not args.job:
                parser.error("run에는 작업 JSON 경로가 필요합니다.")
            code, response = run(args)
        elif args.action == "setup":
            if args.engine or args.python:
                parser.error("setup은 별도 스킬 가상환경을 구성합니다.")
            response, code = setup(args.hancom_module), 0
        else:
            command, _, runtime = choose_runtime(args)
            response, code = {"success": True, "version": VERSION,
                              "runtime": runtime, "command": command}, 0
            if args.hancom:
                with tempfile.TemporaryDirectory(prefix="narae-doctor-") as temporary:
                    job = Path(temporary) / "doctor.json"
                    job.write_text('{"action":"hancom_capabilities"}', encoding="utf-8")
                    args.job, args.result = str(job), None
                    code, diagnostic = run(args)
                    if not diagnostic.get("success"):
                        response.update(success=False, hancom_error=diagnostic.get("error"))
                    else:
                        response["hancom"] = {"version": diagnostic["hancom_version"], "security_module_registered": diagnostic["security_module_registered"],
                                              "com_members": len(diagnostic["members"]), "parameter_sets": len(diagnostic["parameter_sets"]),
                                              "documented_actions": len(diagnostic["action_catalog"]["identifiers"])}
                        if not diagnostic["security_module_registered"]: response["success"], code = False, 1
    except (OSError, ValueError, json.JSONDecodeError) as error:
        response, code = {"success": False, "error": str(error)}, 1
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
