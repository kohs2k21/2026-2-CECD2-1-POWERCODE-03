# CPU 소량 시험 전용 이미지. ESB·LLM·GPU 서비스를 시작하지 않는다.
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY iot_sfc/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY pg_queue.py ./pg_queue.py
COPY iot_sfc ./iot_sfc
ENTRYPOINT ["python", "-m", "iot_sfc"]
