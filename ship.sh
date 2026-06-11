#!/bin/bash
set -e

cd "$(dirname "$0")"

# --- Сообщение коммита ---
MSG="${1:-update}"

# --- Git ---
echo "📦 Коммитим..."
git add -A

# Если нечего коммитить — просто пушим
if git diff --cached --quiet; then
  echo "  (нечего коммитить, просто пушим)"
else
  git commit -m "$MSG"
fi

echo "🚀 Пушим на GitHub..."
git push

# --- Деплой на сервер ---
echo "🌐 Деплоим на сервер..."
bash deploy.sh

echo ""
echo "✅ Готово!"
