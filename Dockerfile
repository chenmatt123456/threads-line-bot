# 使用 Playwright 建議的、匹配我們函式庫版本的最新基礎環境
FROM mcr.microsoft.com/playwright/python:v1.53.0-jammy

# 後續所有內容保持不變
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
ENV PORT 8080
CMD gunicorn -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:${PORT} app:app