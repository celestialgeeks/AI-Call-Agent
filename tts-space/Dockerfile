FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    espeak-ng \
    && rm -rf /var/lib/apt/lists/*

ENV PHONEMIZER_ESPEAK_LIBRARY=/usr/lib/x86_64-linux-gnu/libespeak-ng.so \
    ORT_THREADS=2

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download model weights + voices at build time (faster cold start)
COPY download_models.py .
RUN python download_models.py

COPY app.py .

EXPOSE 7860
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]
