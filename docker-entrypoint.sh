#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${GITHUB_USERNAME:-}" && -n "${GITHUB_PASSWORD:-}" ]]; then
  git config --global url."https://${GITHUB_USERNAME}:${GITHUB_PASSWORD}@github.com/".insteadOf "https://github.com/"
fi

run_streamlit() {
  while true; do
    set +e
    streamlit run /app/app.py \
      --server.address=127.0.0.1 \
      --server.port=8501 \
      --server.headless=true \
      --browser.gatherUsageStats=false
    status=$?
    set -e
    echo "streamlit exited with status ${status}; restarting in 2 seconds" >&2
    sleep 2
  done
}

run_streamlit &

exec nginx -g "daemon off;"
