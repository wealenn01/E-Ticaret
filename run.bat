@echo off
cd /d "%~dp0"

if not exist .venv (
  echo Sanal ortam olusturuluyor...
  python -m venv .venv
)

call .venv\Scripts\activate.bat

python -c "import fastapi, google.genai, PIL" 2>nul
if errorlevel 1 (
  echo Bagimliliklar kuruluyor...
  pip install -r requirements.txt
)

if not exist .env (
  copy .env.example .env
  echo .env olusturuldu - GEMINI_API_KEY doldurmayi unutma.
)

set PORT=%1
if "%PORT%"=="" set PORT=8000

echo Eski sunucular kapatiliyor...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'uvicorn.*app\.main:app' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo Port %PORT% kontrol ediliyor...
.venv\Scripts\python.exe -c "import httpx; r=httpx.get('http://127.0.0.1:%PORT%/api/status', timeout=2); b=r.json().get('review_scraper_build',''); raise SystemExit(0 if b=='ldjson-v2' else 1)" 2>nul
if %errorlevel%==0 (
  echo http://127.0.0.1:%PORT% zaten guncel sunucu ile calisiyor.
  exit /b 0
)

.venv\Scripts\python.exe -c "import socket; s=socket.socket(); import sys; p=int(sys.argv[1]); e=s.connect_ex(('127.0.0.1',p)); s.close(); sys.exit(0 if e==0 else 1)" %PORT% 2>nul
if %errorlevel%==0 (
  echo UYARI: Port %PORT% mesgul veya eski sunucu takili. 8765 deneniyor...
  set PORT=8765
)

echo http://127.0.0.1:%PORT%/panel adresinde baslatiliyor...
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port %PORT% --reload
