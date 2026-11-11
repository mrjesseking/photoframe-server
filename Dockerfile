# Photo Frame server. Pure Python standard library: nothing to compile, so the same
# image builds and runs on amd64 (NAS, Linux server) and arm64 / arm/v7 (Raspberry Pi 3/4/5).
FROM python:3.12-alpine

LABEL org.opencontainers.image.source="https://github.com/mrjesseking/photoframe-server" \
      org.opencontainers.image.description="Photo Frame server: serves a photo folder to the Apple TV app"
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PHOTO_DIR=/photos \
    DATA_DIR=/data \
    PORT=8080

RUN addgroup -S -g 10001 photoframe \
 && adduser -S -u 10001 -G photoframe photoframe \
 && mkdir -p /data /photos \
 && chown photoframe:photoframe /data

WORKDIR /app
COPY photoserver ./photoserver

USER photoframe
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/v1/health' % os.environ.get('PORT','8080'), timeout=4)" || exit 1

CMD ["python", "-m", "photoserver"]
