FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]"

COPY . .

# 版本注入: ARG 放在 COPY 之后、ENV 之前。
# COPY 层走缓存没关系 —— 只要 GIT_COMMIT 值变了，Docker 就会重新执行
# 从这行 ARG 往后的所有层，ENV 里会烤进新的 commit hash。
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
# 迁移失败时 sleep 30 再退出, 避免 restart:unless-stopped 紧抠重启循环 (吃 CPU + 刷日志)
CMD ["sh", "-c", "alembic upgrade head || { echo '[startup] alembic 迁移失败, 30s 后退出以避免紧抠重启循环'; sleep 30; exit 1; }; exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"]
