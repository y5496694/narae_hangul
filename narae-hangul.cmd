@echo off
setlocal
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"
set "NARAE_PYTHON=%~dp0runtime\python\python.exe"
if not exist "%NARAE_PYTHON%" set "NARAE_PYTHON=python"
set "NARAE_CLI=%~dp0skill\scripts\narae.py"
if not exist "%NARAE_CLI%" for /d %%D in ("%~dp01_*") do set "NARAE_CLI=%%D\narae-hangul\scripts\narae.py"
"%NARAE_PYTHON%" -I -X utf8 "%NARAE_CLI%" %*
exit /b %errorlevel%
