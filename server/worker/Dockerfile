FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY worker/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --uid 10001 --create-home worker \
    && mkdir -p /artifacts/tmp && chown -R worker:worker /artifacts
COPY --chown=worker:worker worker/ ./
USER worker
CMD ["python", "-m", "detector.runner"]
