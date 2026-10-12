FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --system pasarbot && useradd --system --gid pasarbot --home /app pasarbot
WORKDIR /app

COPY pyproject.toml README.md ./
COPY pasarbot ./pasarbot
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install --upgrade pip && pip install .

USER pasarbot
CMD ["pasarbot"]
