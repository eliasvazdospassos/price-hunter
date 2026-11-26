FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PRICE_HUNTER_ENV=production \
    PRICE_HUNTER_DB_BACKEND=auto \
    PORT=8080

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY price_hunter ./price_hunter
COPY static ./static
COPY run.py ./

RUN useradd --create-home --shell /bin/bash appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

CMD ["python3", "run.py"]
