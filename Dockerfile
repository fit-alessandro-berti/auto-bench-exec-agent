FROM python:3.12-slim

ARG GITHUB_USERNAME=""
ARG GITHUB_PASSWORD=""

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git nginx ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

RUN set -eux; \
    clone_repo() { \
      repo_name="$1"; \
      if [ -n "$GITHUB_USERNAME" ] && [ -n "$GITHUB_PASSWORD" ]; then \
        git clone "https://${GITHUB_USERNAME}:${GITHUB_PASSWORD}@github.com/fit-alessandro-berti/${repo_name}.git" "/app/${repo_name}"; \
      else \
        git clone "https://github.com/fit-alessandro-berti/${repo_name}.git" "/app/${repo_name}"; \
      fi; \
    }; \
    clone_repo pm-llm-benchmark; \
    clone_repo hallucin-pm-bench; \
    clone_repo d-bench; \
    clone_repo llm-dreams-benchmark

COPY . /app/
COPY nginx.conf /etc/nginx/nginx.conf

RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 80 443

CMD ["/app/docker-entrypoint.sh"]
