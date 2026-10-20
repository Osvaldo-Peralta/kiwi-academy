# Kiwi Academy — imagen de producción
# Funciona en: Render, Railway, Fly.io, cualquier VPS con Docker, y local.
#
#   docker build -t kiwi-academy .
#   docker run -p 8000:8000 -v kiwi-data:/app/data kiwi-academy
#
# El volumen en /app/data persiste el progreso de los estudiantes.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    KIWI_DATA_DIR=/app/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY content ./content
COPY frontend ./frontend

RUN mkdir -p /app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
