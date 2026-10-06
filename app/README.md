# app/

The Media Strategy Planner application: `api/` (FastAPI + vel + mesh) and `web/` (React + Vite), wired together by `docker-compose.yml`.

See the [root README](../README.md) for setup, running, configuration, tests and evals. In short:

```bash
cp .env.example .env      # set OPENAI_API_KEY
docker compose up --build # http://localhost:8080 (or run it from the repo root)
```
