# auto-bench-exec-agent

Automatic Benchmark Execution Agent for:

- `llm-dreams-benchmark`
- `pm-llm-benchmark`
- `pmllmbench-lrms-reasoning-analysis`
- `hallucin-pm-bench`
- `d-bench`

## Streamlit Application

The app accepts an LLM name, provider, and benchmark selection on the main screen. Advanced configuration is hidden by default and remains available in the expanded settings panel.

When a benchmark run is active, the app stores its state in `state/status.json`. Reloading the page keeps the submitted configuration disabled, shows a spinner, displays the current log tail, and offers stop and spawned-thread cleanup buttons. The Streamlit process only launches a background worker process; the worker runs the benchmarks and limits Python thread pools and raw Python threads to avoid benchmark subprocesses exhausting the web process. Every run carries a unique job id inherited by its subprocesses, allowing automatic and manual cleanup to terminate orphaned descendants and their threads.

Run locally:

```bash
pip install -r requirements.txt
streamlit run app.py
```

The app looks for the benchmark repositories first inside this directory and then in the parent directory. The Docker image clones them into `/app` during build.

The CLI runs all benchmarks by default. Use `--benchmark` repeatedly or `--benchmarks` with a comma-separated list to select a subset:

```bash
python cli_execute.py my-model --benchmark d-bench --benchmark hallucin-pm-bench
python cli_execute.py my-model --benchmarks d-bench,hallucin-pm-bench
python cli_execute.py my-model --benchmark d-bench --max-worker-threads 8
python cli_execute.py my-model --disable-git-clean
```

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

`AUTO_BENCH_MAX_WORKERS` is the default upper bound for Python worker threads in each benchmark subprocess. It defaults to 60, accepts any integer of at least 1, and can be overridden per run with the advanced configuration panel or `--max-worker-threads`. `ThreadPoolExecutor` pools asking for fewer workers keep their smaller value; pools asking for more are capped. Native-library thread pools are set to one thread to avoid multiplying the Python-level concurrency.

The executor and every selected benchmark normally run `git reset --hard HEAD`, `git clean -x -f`, and `git pull` before a run. Select **Disable git clean** in advanced configuration, or pass `--disable-git-clean`, to forward the option to every benchmark and skip only each repository's `git clean` command. The option is off by default.

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
https://github.com/fit-alessandro-berti/pmllmbench-lrms-reasoning-analysis
https://github.com/fit-alessandro-berti/hallucin-pm-bench
https://github.com/fit-alessandro-berti/d-bench
https://github.com/fit-alessandro-berti/llm-dreams-benchmark
```

During image build, `GITHUB_USERNAME` and `GITHUB_PASSWORD` are used through Git's credential helper to clone the benchmark repositories without storing credentials in their remotes.
At runtime, the container writes the same credentials to Git's credential helper store, normalizes the benchmark remotes to `https://github.com/...`, and configures `user.name` / `user.email` from `GIT_COMMIT_USER_NAME` and `GIT_COMMIT_USER_EMAIL` so `git pull`, `git commit`, and `git push` work by default.
