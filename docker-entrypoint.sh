#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${GITHUB_USERNAME:-}" && -n "${GITHUB_PASSWORD:-}" ]]; then
  git config --global url."https://${GITHUB_USERNAME}:${GITHUB_PASSWORD}@github.com/".insteadOf "https://github.com/"
fi

streamlit run /app/app.py \
  --server.address=127.0.0.1 \
  --server.port=8501 \
  --server.headless=true \
  --browser.gatherUsageStats=false &

exec nginx -g "daemon off;"
