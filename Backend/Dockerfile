FROM python:3.10-slim

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Jakarta \
    PADDLEOCR_HOME=/app/.paddleocr

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libsm6 libxrender1 libxext6 libgl1-mesa-glx ffmpeg \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir torch==2.1.0 torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && rm -rf /root/.cache/pip

COPY . .

RUN mkdir -p /app/.paddleocr && chmod -R 777 /app/.paddleocr

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]

