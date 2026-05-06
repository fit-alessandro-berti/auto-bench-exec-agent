FROM python:3.12-slim

ARG GITHUB_USERNAME=""
ARG GITHUB_PASSWORD=""

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git nginx apache2-utils fail2ban iptables ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

RUN set -eux; \
    if [ -n "$GITHUB_USERNAME" ] && [ -n "$GITHUB_PASSWORD" ]; then \
      git config --global credential.helper "store --file=/root/.git-credentials"; \
      GITHUB_USERNAME="$GITHUB_USERNAME" GITHUB_PASSWORD="$GITHUB_PASSWORD" python3 -c "import os,pathlib,urllib.parse; u=urllib.parse.quote(os.environ['GITHUB_USERNAME'], safe=''); p=urllib.parse.quote(os.environ['GITHUB_PASSWORD'], safe=''); path=pathlib.Path('/root/.git-credentials'); path.write_text(f'https://{u}:{p}@github.com\n', encoding='utf-8'); path.chmod(0o600)"; \
    fi; \
    clone_repo() { \
      repo_name="$1"; \
      git clone "https://github.com/fit-alessandro-berti/${repo_name}.git" "/app/${repo_name}"; \
    }; \
    clone_repo pm-llm-benchmark; \
    clone_repo pmllmbench-lrms-reasoning-analysis; \
    clone_repo hallucin-pm-bench; \
    clone_repo d-bench; \
    clone_repo llm-dreams-benchmark; \
    rm -f /root/.git-credentials; \
    git config --global --unset credential.helper || true

COPY . /app/
COPY nginx.conf /etc/nginx/nginx.conf
COPY fail2ban/jail.local /etc/fail2ban/jail.local

RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 80 443

CMD ["/app/docker-entrypoint.sh"]
