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

echo http://127.0.0.1:%PORT% adresinde baslatiliyor...
uvicorn app.main:app --reload --reload-exclude ".venv/*" --reload-exclude "data/*" --reload-exclude "uploads/*" --port %PORT%
