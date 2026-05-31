FROM python:3.11-slim

# Pythonの標準出力・標準エラー出力のバッファリングを無効化
# Cloud Runのログを遅延なく出力させる
ENV PYTHONUNBUFFERED=1
# pycファイルの生成を無効化
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ./app ./app

ENV PORT=8080

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
