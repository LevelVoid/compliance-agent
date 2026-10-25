FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TEMPORAL_ADDRESS=temporal:7233

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Worker by default; override for one-shot CLI runs:
#   docker compose run --rm worker python audit.py run --path fixtures/bad
CMD ["python", "-m", "workflows.worker"]
