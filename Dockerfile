FROM python:3.11-slim AS builder

ARG INSTALL_EXTRAS=ops,gemini
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates \
  && rm -rf /var/lib/apt/lists/* \
  && python -m venv "${VIRTUAL_ENV}"

COPY pyproject.toml README.md /app/
COPY app /app/app

RUN python -m pip install --upgrade pip \
  && pip install --no-cache-dir -e ".[${INSTALL_EXTRAS}]"


FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates \
  && rm -rf /var/lib/apt/lists/* \
  && useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser

COPY --from=builder /opt/venv /opt/venv

# Copy only runtime-required repo content (avoid copying local secrets/env files into image).
COPY app /app/app
COPY dashboard /app/dashboard
COPY docs /app/docs
COPY scripts /app/scripts
COPY orchestration /app/orchestration
COPY README.md Makefile main.py start_outbound_dialing /app/

RUN mkdir -p /app/skills
RUN chown -R appuser:appuser /app /opt/venv

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=5s --retries=5 --start-period=20s \
  CMD python -c "import urllib.request; raise SystemExit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).getcode()==200 else 1)"

CMD ["sh", "-c", "exec uvicorn app.server:app --host 0.0.0.0 --port 8080 --workers ${UVICORN_WORKERS:-2}"]
