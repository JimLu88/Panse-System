FROM python:3.11-slim
WORKDIR /srv
RUN pip install --no-cache-dir fastapi==0.136.1 uvicorn==0.46.0 python-multipart==0.0.29 pillow==12.2.0
RUN groupadd -g 10001 fuge && useradd -u 10001 -g fuge -M fuge
COPY backend/app/isolated_store.py backend/app/isolated_store_pricing.py backend/app/isolated_store_recovery.py backend/app/isolated_store.html backend/app/isolated_store_catalog.py backend/app/isolated_store_setup.py /srv/app/
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
CMD ["uvicorn","app.isolated_store:environment_app","--factory","--host","0.0.0.0","--port","8290","--no-proxy-headers"]
