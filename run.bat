@echo off
setlocal

cd /d "%~dp0"

python -m pip install -r backend\requirements.txt
if errorlevel 1 (
  echo Falha ao instalar dependencias.
  exit /b 1
)

start "" cmd /c "timeout /t 3 /nobreak >nul && start "" http://127.0.0.1:8000"
python backend\app.py
