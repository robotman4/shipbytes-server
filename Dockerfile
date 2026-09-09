FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN groupadd --gid 10001 shipbytes && useradd --uid 10001 --gid 10001 --no-create-home shipbytes && mkdir /data && chown shipbytes:shipbytes /data
COPY shipbytes shipbytes
COPY migrations migrations
COPY alembic.ini .
USER shipbytes
EXPOSE 8000
CMD ["uvicorn", "shipbytes.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log", "--no-proxy-headers"]
FROM base AS test
USER root
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY tests tests
COPY scripts scripts
USER shipbytes
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
FROM base AS production
