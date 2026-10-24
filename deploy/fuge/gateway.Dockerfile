FROM nginx:alpine
COPY deploy/fuge/gateway.conf /etc/nginx/nginx.conf
USER 101:101
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
