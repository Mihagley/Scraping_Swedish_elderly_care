@echo off
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
echo Could not start the interface. Check the error above and that Python 3.11 or newer is installed.
pause
exit /b 1
