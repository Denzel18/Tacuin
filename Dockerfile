FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    TZ=Europe/Rome \
    HOST=0.0.0.0 \
    PORT=8080 \
    DATA_DIR=/data

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --create-home app \
 && mkdir -p /data && chown app:app /data

# unica libreria esterna: crittografia per le notifiche push (Web Push / VAPID)
RUN pip install --no-cache-dir cryptography==43.0.3

WORKDIR /app
COPY *.py ./
COPY web ./web
COPY vendor ./vendor

USER app
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT','8080'), timeout=3)" || exit 1

CMD ["python", "server.py"]
