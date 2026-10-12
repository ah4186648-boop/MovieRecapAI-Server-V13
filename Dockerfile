FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk fonts-noto-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
ENV PORT=10000
ENV PUBLIC_BASE_URL=http://127.0.0.1:10000
EXPOSE 10000
CMD ["sh","-c","uvicorn server:app --host 0.0.0.0 --port ${PORT:-10000}"]
