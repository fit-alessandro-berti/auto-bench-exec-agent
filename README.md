# auto-bench-exec-agent

Automatic Benchmark Execution Agent for:

- `pm-llm-benchmark`
- `hallucin-pm-bench`
- `d-bench`
- `llm-dreams-benchmark`

## Streamlit Application

The app accepts an LLM name and provider on the main screen. Advanced configuration is hidden by default and remains available in the expanded settings panel.

When a benchmark run is active, the app stores its state in `state/status.json`. Reloading the page keeps the submitted configuration disabled, shows a spinner, displays the current log tail, and offers a stop button for the active execution. The Streamlit process only launches a background worker process; the worker runs the benchmarks and sets Python thread pools to the configured worker count while limiting raw Python threads to avoid benchmark subprocesses exhausting the web process.

Run locally:

```bash
pip install -r requirements.txt
streamlit run app.py
```

The app looks for the four benchmark repositories first inside this directory and then in the parent directory. The Docker image clones them into `/app` during build.

## Docker

The Docker deployment is protected by browser basic authentication through Nginx. Set `APP_USERNAME` and `APP_PASSWORD` in `docker-compose.yml`; the browser asks for them once per browser session for the same host.
The image also starts fail2ban for Nginx basic-auth failures. By default, five failed login attempts from the same IP within ten minutes are banned for one hour, with repeated bans increasing up to 24 hours. The compose file grants `NET_ADMIN` so fail2ban can install its iptables rule inside the container network namespace. Set `FAIL2BAN_ENABLED=0` to disable this layer.

Create self-signed certificates in the current folder:

```bash
openssl req -x509 -nodes -newkey rsa:4096 \
  -keyout privkey.pem \
  -out fullchain.pem \
  -days 365 \
  -subj "/CN=localhost"
```

The certificate files are mounted into the container at runtime:

```text
./fullchain.pem -> /certs/fullchain.pem
./privkey.pem -> /certs/privkey.pem
```

They are excluded from the Docker build context and are not copied into the image.

Fill in the credentials and API keys directly in `docker-compose.yml`. The same GitHub credentials are present both under `build.args` for image-build cloning and under `environment` for runtime `git pull` / `git push`.

`AUTO_BENCH_MAX_WORKERS` controls the Python worker count used by benchmark subprocesses. Values lower than 60 are clamped to 60; higher values are allowed. It can also be changed per run from the advanced configuration panel. Explicit single-worker pools remain single-threaded; other `ThreadPoolExecutor` pools are normalized to this value by default. Set `AUTO_BENCH_FORCE_CONFIGURED_WORKERS=0` to use the value only as an upper cap.

Docker receives API keys in two ways:

- API key variables are defined in the `environment` section of `docker-compose.yml`.
- At container startup, known API key variables are also written to `/app/api_*.txt` files, for benchmarks that expect file-based keys such as `/app/api_openrouter.txt`.

Build and start:

```bash
docker compose up --build
```

The application is exposed on:

```text
http://localhost
https://localhost
```

During image build, Docker clones these repositories into `/app`:

```text
https://github.com/fit-alessandro-berti/pm-llm-benchmark
https://github.com/fit-alessandro-berti/hallucin-pm-bench
https://github.com/fit-alessandro-berti/d-bench
https://github.com/fit-alessandro-berti/llm-dreams-benchmark
```

During image build, `GITHUB_USERNAME` and `GITHUB_PASSWORD` are used through Git's credential helper to clone the benchmark repositories without storing credentials in their remotes.
At runtime, the container writes the same credentials to Git's credential helper store, normalizes the benchmark remotes to `https://github.com/...`, and configures `user.name` / `user.email` from `GIT_COMMIT_USER_NAME` and `GIT_COMMIT_USER_EMAIL` so `git pull`, `git commit`, and `git push` work by default.
