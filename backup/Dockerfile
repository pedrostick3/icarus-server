FROM python:3.13-alpine

RUN apk add --no-cache tzdata
WORKDIR /app
COPY backup.py /app/backup.py

ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    BACKUP_DIR=/backups

ENTRYPOINT ["python", "/app/backup.py"]
