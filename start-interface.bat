@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 goto :failed
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m streamlit run streamlit_app.py --server.address localhost
if errorlevel 1 goto :failed
exit /b 0
:failed
echo Gränssnittet kunde inte startas. Kontrollera felet ovan och att Python 3.11 eller senare är installerat.
pause
exit /b 1
