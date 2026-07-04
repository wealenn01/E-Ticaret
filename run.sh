#!/usr/bin/env bash
# Trendyol Listeleme Agent'ı — tek komutla kurulum + başlatma
# Kullanım: ./run.sh          (varsayılan port 8000)
#          ./run.sh 8080      (farklı port)
set -euo pipefail
cd "$(dirname "$0")"

PORT="${1:-8000}"

# 1) Sanal ortam yoksa oluştur
if [ ! -d .venv ]; then
  echo "📦 Sanal ortam oluşturuluyor (.venv)…"
  python3 -m venv .venv
fi
source .venv/bin/activate

# 2) Bağımlılıklar eksikse kur (hızlı kontrol: fastapi + google-genai)
if ! python -c "import fastapi, google.genai" >/dev/null 2>&1; then
  echo "📦 Bağımlılıklar kuruluyor…"
  pip install -q -r requirements.txt
fi

# 3) .env yoksa şablondan kopyala
if [ ! -f .env ]; then
  cp .env.example .env
  echo "⚠️  .env oluşturuldu — GEMINI_API_KEY'i doldurmayı unutma (https://aistudio.google.com)."
fi

echo "🚀 http://127.0.0.1:${PORT} adresinde başlatılıyor… (durdurmak için Ctrl+C)"
exec uvicorn app.main:app --reload --port "$PORT"
