FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN addgroup --system persona && adduser --system --ingroup persona persona

COPY pyproject.toml README.md ./
COPY app ./app
COPY bot ./bot
RUN python -m pip install --no-cache-dir .

COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts

RUN mkdir -p /app/data && chown -R persona:persona /app

USER persona
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "scripts/healthcheck.py"]

ENTRYPOINT ["python", "scripts/container_entrypoint.py"]
CMD ["python", "-m", "bot.main"]
