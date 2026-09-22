FROM python:3.13-slim

# tesseract is a runtime dependency, not a Python one: pytesseract shells out to
# the binary, so a pip-only image fails at the first scanned PDF page.
# libgomp1 is needed by onnxruntime, which fastembed pulls in.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Copied first so the (slow, ~700MB of wheels) install layer is reused whenever
# only application code changed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/ ./agent/
COPY app/ ./app/
COPY migrations/ ./migrations/

# Settings resolve storage paths relative to BACKEND_DIR (/app), so /app/data is
# the mount point the compose volume has to land on.
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
