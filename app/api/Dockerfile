# Chat API: FastAPI + vel on Python 3.11, dependencies from uv.lock (vel-ai from PyPI).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /bin/uv
WORKDIR /srv

# Unprivileged user that run_python_analysis drops to: it can read case.db and the
# venv, but not /data (chat.db) or the API process's environment. It gets a fixed,
# unusual uid because the kernel counts RLIMIT_NPROC per uid across ALL containers on
# the host: a common system uid (e.g. 999, used by the postgres and redis images)
# would share its process cap with those containers' processes.
RUN groupadd --gid 61999 sandbox \
 && useradd --uid 61999 --gid 61999 --no-create-home --shell /usr/sbin/nologin sandbox \
 && mkdir -p /srv/case

# Dependency layer first, so code changes don't reinstall packages
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

# The four case-study source files; case.db is rebuilt from them on every start.
COPY case_data ./case_data

ENV PATH="/srv/.venv/bin:$PATH" \
    APP_HOST=0.0.0.0 \
    APP_PORT=7666 \
    APP_DB_PATH=/data/chat.db \
    APP_CASE_DATA_DIR=/srv/case_data \
    APP_CASE_DB_PATH=/srv/case/case.db \
    APP_EXAMPLE_CHATS=1
VOLUME /data
EXPOSE 7666

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7666/health', timeout=2)"

# One worker: SQLite is shared through a single locked connection.
# /data is a named volume, which keeps the permissions it was created with, so it is
# locked to root on every start rather than at build time.
CMD ["sh", "-c", "chmod 700 /data && exec uvicorn chat_api.main:build_app --factory --host 0.0.0.0 --port ${APP_PORT} --workers 1"]
