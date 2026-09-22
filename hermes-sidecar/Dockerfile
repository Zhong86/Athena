FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Loopback only. This service writes Hermes' credential files, so reaching it
# should require an SSH tunnel or a TLS proxy in front -- not a published port.
EXPOSE 8700

CMD ["uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8700"]
