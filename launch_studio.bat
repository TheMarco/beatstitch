@echo off
cd /d "%~dp0"
if exist "dist\DJ Studio\DJ Studio.exe" (
    start "" "dist\DJ Studio\DJ Studio.exe" --data-dir "%~dp0outputs\studio" %*
    exit /b
)
.venv\Scripts\pythonw.exe desktop_app.py --data-dir outputs\studio %*
