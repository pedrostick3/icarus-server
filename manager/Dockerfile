FROM python:3.13-alpine

RUN apk add --no-cache tzdata rclone
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY icarus_manager ./icarus_manager

ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    BACKUP_DIR=/backups \
    CONTROL_DIR=/control \
    RCLONE_CONFIG=/config/rclone/rclone.conf

ENTRYPOINT ["python", "-m", "icarus_manager"]
CMD ["serve"]
