FROM python:3.12.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y graphviz libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

COPY backend/pyproject.toml backend/README.md ./
COPY backend/src ./src
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini

RUN --mount=type=cache,target=/root/.cache/pip python -m pip install . \
    && groupadd --gid 10001 decisionflow \
    && useradd --uid 10001 --gid decisionflow --no-create-home --shell /usr/sbin/nologin decisionflow

FROM base AS runtime

USER 10001:10001

CMD ["decisionflow", "--help"]

FROM base AS tools

RUN --mount=type=cache,target=/root/.cache/pip python -m pip install ".[dev]"

COPY backend/tests ./tests

CMD ["decisionflow", "--help"]

FROM base AS mlflow-runtime

# The named artifact volume is initialized by Docker as root-owned. This dedicated image is
# intentionally rootful; the API and Temporal worker use the non-root runtime stage above.
CMD ["mlflow", "--help"]
