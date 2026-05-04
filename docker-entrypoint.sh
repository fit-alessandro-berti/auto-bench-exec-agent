#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${APP_USERNAME:-}" || -z "${APP_PASSWORD:-}" ]]; then
  echo "APP_USERNAME and APP_PASSWORD must be set to protect the web application." >&2
  exit 1
fi
if [[ "${APP_USERNAME}" == *:* ]]; then
  echo "APP_USERNAME must not contain ':'." >&2
  exit 1
fi

printf '%s\n' "${APP_PASSWORD}" | htpasswd -i -B -c /etc/nginx/.htpasswd "${APP_USERNAME}" >/dev/null
chmod 644 /etc/nginx/.htpasswd

configure_git() {
  git config --global user.name "${GIT_COMMIT_USER_NAME:-${GITHUB_USERNAME:-auto-bench-exec-agent}}"
  git config --global user.email "${GIT_COMMIT_USER_EMAIL:-${GITHUB_USERNAME:-auto-bench-exec-agent}@users.noreply.github.com}"

  if [[ -n "${GITHUB_USERNAME:-}" && -n "${GITHUB_PASSWORD:-}" ]]; then
    git config --global credential.helper "store --file=/root/.git-credentials"
    python3 - <<'PY'
import os
import pathlib
import urllib.parse

username = os.environ["GITHUB_USERNAME"]
password = os.environ["GITHUB_PASSWORD"]
credential = "https://{}:{}@github.com\n".format(
    urllib.parse.quote(username, safe=""),
    urllib.parse.quote(password, safe=""),
)
credentials_path = pathlib.Path("/root/.git-credentials")
credentials_path.write_text(credential, encoding="utf-8")
credentials_path.chmod(0o600)
PY
  fi

  for repo in \
    /app/pm-llm-benchmark \
    /app/hallucin-pm-bench \
    /app/d-bench \
    /app/llm-dreams-benchmark; do
    if [[ -d "${repo}/.git" ]]; then
      repo_name="$(basename "${repo}")"
      git -C "${repo}" remote set-url origin "https://github.com/fit-alessandro-berti/${repo_name}.git"
    fi
  done
}

configure_git

write_api_key_file() {
  local env_name="$1"
  local file_name="$2"
  local value="${!env_name:-}"

  if [[ -n "${value}" ]]; then
    umask 077
    printf '%s' "${value}" > "/app/${file_name}"
  fi
}

write_api_key_file OPENROUTER_API_KEY api_openrouter.txt
write_api_key_file OPENAI_API_KEY api_openai.txt
write_api_key_file ANTHROPIC_API_KEY api_anthropic.txt
write_api_key_file GOOGLE_API_KEY api_google.txt
write_api_key_file GROK_API_KEY api_grok.txt
write_api_key_file MISTRAL_API_KEY api_mistral.txt
write_api_key_file DEEPINFRA_API_KEY api_deepinfra.txt
write_api_key_file QWEN_API_KEY api_qwen.txt
write_api_key_file NVIDIA_API_KEY api_nvidia.txt
write_api_key_file PERPLEXITY_API_KEY api_perplexity.txt
write_api_key_file GROQ_API_KEY api_groq.txt

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
