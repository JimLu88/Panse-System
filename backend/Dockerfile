FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# 工厂下单图 HTML → 图片 (用户拍板 2026-06-12: 下单图推飞书成图片):
#   官方 wkhtmltox patched .deb 自带无头 Qt(wkhtmltoimage); fonts-noto-cjk 中文字体; xvfb 兜底无头显示。
#   bookworm 默认源已无 wkhtmltopdf, 故钉 bookworm 基础镜像 + 装官方 .deb。
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget ca-certificates fonts-noto-cjk fontconfig xvfb \
    && wget -q https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-3/wkhtmltox_0.12.6.1-3.bookworm_amd64.deb -O /tmp/wk.deb \
    && apt-get install -y --no-install-recommends /tmp/wk.deb \
    && rm /tmp/wk.deb \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]"

COPY backend/ ./
# 根目录活动报名规则既保留为仓库内唯一可编辑源，也随 API 镜像固化。
# 生产 NAS 即使尚未增加 compose bind mount，程序仍读取同一提交里的规则；
# 若 compose 已挂载该文件，则挂载版本覆盖镜像内副本。
COPY TAOBAO_CAMPAIGN_SIGNUP_POLICY.json /app/TAOBAO_CAMPAIGN_SIGNUP_POLICY.json

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
CMD ["sh", "-c", "alembic upgrade head || { echo '[startup] alembic 迁移失败, 30s 后退出以避免紧抠重启循环'; sleep 30; exit 1; }; python -m app.cli.campaign_seed_sku_slots || { echo '[startup] SKU基准固化失败'; exit 1; }; exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"]
