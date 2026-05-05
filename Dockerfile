FROM python:3.11-slim

# Güvenlik: root olmayan kullanıcı
RUN groupadd -r ptfuser && useradd -r -g ptfuser ptfuser

WORKDIR /app

# Bağımlılıkları kur
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Uygulama kodunu kopyala
COPY backend/ ./backend/
COPY jobs/ ./jobs/

# Veri dizini
RUN mkdir -p /app/data /app/.venvs && chown -R ptfuser:ptfuser /app

USER ptfuser

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]

