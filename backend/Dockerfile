FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir $(python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); print(' '.join(d['project']['dependencies']))")

# Install bundled Chromium and system dependencies
RUN playwright install chromium && playwright install-deps chromium

COPY . .

ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_CHANNEL=""
ENV HEADLESS_AUTH=1
ENV DISCOGS_BROWSER_DATA=/data

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
