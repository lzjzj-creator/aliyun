# 知脉主后端：FastAPI（路由/RAG问答/改写）— CloudBase 云托管用
FROM python:3.10-slim-bookworm

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN python -m playwright install --with-deps chromium \
    && useradd --create-home --uid 10001 app

COPY . .
USER app

# CloudBase 通过服务端口（默认 80）转发流量；本地跑时 PORT=8788
ENV PORT=80
EXPOSE 80

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT}
