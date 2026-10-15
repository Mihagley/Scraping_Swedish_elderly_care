@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 goto failed
if exist ".venv\Scripts\python.exe" goto install
python -c "import sys; sys.exit(sys.version_info < (3, 11))"
if errorlevel 1 goto python_missing
python -m venv .venv
if errorlevel 1 goto failed
:install
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m streamlit run streamlit_app.py --server.address localhost
if errorlevel 1 goto failed
popd
exit /b 0
:python_missing
echo Installera Python 3.11 eller senare.
:failed
echo Installationen eller starten misslyckades. Se felet ovan.
pause
exit /b 1
