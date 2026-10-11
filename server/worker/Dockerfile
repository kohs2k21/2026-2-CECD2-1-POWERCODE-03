FROM python:3.12-slim-bookworm
ARG SERVICE
RUN case "$SERVICE" in collector|detector|trainer|llm) ;; *) exit 2 ;; esac
WORKDIR /app/worker
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 POWERCODE_SERVICE=${SERVICE}
COPY worker/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY worker/pg_queue.py ./
COPY worker/common/ ./common/
COPY worker/collector/ ./collector/
COPY worker/detector/ ./detector/
COPY worker/trainer/ ./trainer/
COPY contracts/ /app/contracts/
CMD ["python", "-m", "common.runtime_guard"]
