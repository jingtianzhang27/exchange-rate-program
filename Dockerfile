FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

# The official Python image supplies CA roots and the IANA timezone database.
# Fail the build if either prerequisite is missing; never bypass TLS verification.
RUN python -c "import ssl; from zoneinfo import ZoneInfo; assert ssl.create_default_context().get_ca_certs(); ZoneInfo('Asia/Shanghai')" \
    && groupadd --gid 10001 rates \
    && useradd --uid 10001 --gid rates --no-create-home rates \
    && mkdir -p /data \
    && chown rates:rates /data

WORKDIR /app
COPY --chown=rates:rates backend/ ./backend/

USER rates
EXPOSE 8000

# Liveness only: inspect /health.collection_error separately for quote readiness.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).read()"

CMD ["python", "-m", "backend.server", "--host", "0.0.0.0", "--port", "8000", "--db", "/data/rates.sqlite3"]
