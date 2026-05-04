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

The Docker deployment is protected by browser basic authentication through Nginx. Set `APP_USERNAME` and `APP_PASSWORD` in `.env`; the browser asks for them once per browser session for the same host.

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

Create a `.env` file from the example and fill in the credentials and API keys you need:

```bash
cp .env.example .env
```

```bash
APP_USERNAME=admin
APP_PASSWORD=change-this-password
GITHUB_USERNAME=your-github-username
GITHUB_PASSWORD=your-github-token-or-password
OPENROUTER_API_KEY=...
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
GOOGLE_API_KEY=...
GROK_API_KEY=...
MISTRAL_API_KEY=...
DEEPINFRA_API_KEY=...
QWEN_API_KEY=...
NVIDIA_API_KEY=...
PERPLEXITY_API_KEY=...
GROQ_API_KEY=...
AUTO_BENCH_MAX_WORKERS=60
AUTO_BENCH_FORCE_CONFIGURED_WORKERS=1
```

`AUTO_BENCH_MAX_WORKERS` controls the Python worker count used by benchmark subprocesses. Values lower than 60 are clamped to 60; higher values are allowed. It can also be changed per run from the advanced configuration panel. Explicit single-worker pools remain single-threaded; other `ThreadPoolExecutor` pools are normalized to this value by default. Set `AUTO_BENCH_FORCE_CONFIGURED_WORKERS=0` to use the value only as an upper cap.

Docker receives API keys in two ways:

- All variables in `.env` are passed into the container through `env_file`.
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

At runtime, `GITHUB_USERNAME` and `GITHUB_PASSWORD` are also passed into the container and configured for `git pull` operations.
