FROM python:3.12-slim

RUN apt-get update && apt-get install -y postgresql-client && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . /app/

RUN mkdir -p /app/static /app/media && \
    chmod -R 777 /app/static /app/media

RUN useradd -m appuser || true
USER appuser

CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "aplus_backend.asgi:application"]