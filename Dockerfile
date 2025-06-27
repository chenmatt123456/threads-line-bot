# 使用微軟官方提供的、預先安裝好 Playwright 的 Python 環境作為基礎
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

# 設定工作目錄
WORKDIR /app

# 先複製「零件清單」，這樣 Render 在快取時會更有效率
COPY requirements.txt .

# 安裝所有 Python 零件
RUN pip install -r requirements.txt

# 複製我們所有的程式碼到容器中
COPY . .

# 設定環境變數，告訴 Gunicorn 在哪個門牌上監聽
ENV PORT 8080

# 最終的啟動指令
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:${PORT}", "app:app"]