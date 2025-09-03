FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV CAIA_PORT=8080
ENV N8N_WEBHOOK_BASE=""
ENV CAIA_AGENT_KEY=""
ENV DEFAULT_WEBHOOK_PATH=""
ENV N8N_UI_URL=""
ENV LOG_LEVEL="info"

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]