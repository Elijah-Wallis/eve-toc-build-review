FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates \
  && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN python -m pip install --upgrade pip \
  && pip install --no-cache-dir pandas playwright \
  && if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi

RUN python -m playwright install --with-deps chromium

ENTRYPOINT ["python", "scripts/applier.py"]
