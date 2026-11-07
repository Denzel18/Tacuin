# Servizio "vision": OpenCV + Tesseract (italiano) per leggere gli scontrini.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PORT=8090 OCR_LANGS=ita+eng OMP_THREAD_LIMIT=2

RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-ita tesseract-ocr-eng libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --create-home app

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /app
COPY app.py ./
USER app
EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=3)" || exit 1

CMD ["python", "app.py"]
