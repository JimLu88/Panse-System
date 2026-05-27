FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]"

COPY . .

# 部署版本: 由 docker compose build --build-arg 注入 (看门狗在 build 前算出 git 信息)
# 烤进镜像的环境变量, 后端 /api/version 读取, 用于核对「当前跑的是哪个 commit」
ARG GIT_COMMIT=unknown
ARG GIT_COMMIT_MSG=""
ARG GIT_COMMIT_DATE=""
ARG GIT_BRANCH=""
ARG BUILD_TIME=""
ENV GIT_COMMIT=$GIT_COMMIT \
    GIT_COMMIT_MSG=$GIT_COMMIT_MSG \
    GIT_COMMIT_DATE=$GIT_COMMIT_DATE \
    GIT_BRANCH=$GIT_BRANCH \
    BUILD_TIME=$BUILD_TIME

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"]
