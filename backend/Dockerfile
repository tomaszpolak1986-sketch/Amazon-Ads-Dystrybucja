# Backend panelu: FastAPI + APScheduler.
#
# Obraz jest jednoetapowy, bo backend nie ma kroku budowania - instalujemy
# zaleznosci i kopiujemy zrodla. Warstwa z requirements idzie przed kodem,
# zeby zmiana w kodzie nie uniewazniala cache pip.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# psycopg potrzebuje libpq; reszta to standardowe kolo
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/

# Nie uruchamiamy jako root - kontener nie ma po co miec tych uprawnien
RUN useradd --create-home --uid 10001 panel \
    && mkdir -p /app/data \
    && chown -R panel:panel /app
USER panel

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", \
     "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
