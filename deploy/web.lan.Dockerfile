# Panse ERP — 自带前端的 LAN web 镜像 (在 PC build, save→load 到群晖; NAS 无前端源码)。
# stage1: node 构建 SPA;stage2: nginx(http) 服务静态站 + 反代 api。
# build: BUILDX_NO_DEFAULT_ATTESTATIONS=1 docker build -f deploy/web.lan.Dockerfile -t panse-system-web:lan .
# (BuildKit 自动用同目录 web.lan.Dockerfile.dockerignore 限定 context, 不影响其它 build)

FROM node:20-alpine AS build
WORKDIR /app
# 512m 容器跑 tsc 会 OOM(exit134); build 阶段虽无内存限, 仍设上限兜底
ENV NODE_OPTIONS=--max-old-space-size=1536
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY frontend/ ./
ARG VITE_FUGE_ORIGIN=""
ENV VITE_FUGE_ORIGIN=$VITE_FUGE_ORIGIN
RUN npm run build

FROM nginx:alpine
ARG GIT_COMMIT=unknown
ARG GIT_COMMIT_DATE=""
ARG BUILD_TIME=""
ARG VITE_BRAND_CODE=PS
ARG VITE_BRAND_NAME="畔色"
ARG VITE_SHOP_NAME="畔色木作"
ARG VITE_FUGE_ORIGIN=""
ARG VITE_PANSE_ORIGIN=""
ENV VITE_BRAND_CODE=$VITE_BRAND_CODE \
    VITE_BRAND_NAME=$VITE_BRAND_NAME \
    VITE_SHOP_NAME=$VITE_SHOP_NAME \
    VITE_FUGE_ORIGIN=$VITE_FUGE_ORIGIN \
    VITE_PANSE_ORIGIN=$VITE_PANSE_ORIGIN
LABEL org.opencontainers.image.revision=$GIT_COMMIT \
      org.opencontainers.image.created=$BUILD_TIME
COPY deploy/nginx.lan.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
# 前端是静态文件，不能复用后端 /api/version 判断自身版本。单独写入构建标记，
# 让发布脚本和运维人员能确认浏览器拿到的 Web 与 API 来自同一 Git 提交。
RUN printf '{"commit":"%s","commit_date":"%s","built_at":"%s"}\n' \
      "$GIT_COMMIT" "$GIT_COMMIT_DATE" "$BUILD_TIME" \
      > /usr/share/nginx/html/build-version.json
EXPOSE 80
