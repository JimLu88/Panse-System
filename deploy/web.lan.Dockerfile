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
RUN npm run build

FROM nginx:alpine
COPY deploy/nginx.lan.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
