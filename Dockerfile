FROM python:3.11-slim-bookworm@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system glycofy \
    && useradd --system --gid glycofy --home-dir /app glycofy

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade \
        pip==26.2.1 \
        setuptools==84.0.0 \
        wheel==0.48.0 \
    && pip install --no-cache-dir --requirement requirements.txt \
    && pip uninstall --yes pip setuptools wheel \
    && python -c "import fastapi, sqlalchemy, uvicorn"

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY ui ./ui
COPY assets ./assets
COPY scripts ./scripts

RUN chown -R glycofy:glycofy /app
USER glycofy

EXPOSE 10000

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers --forwarded-allow-ips='*'"]
