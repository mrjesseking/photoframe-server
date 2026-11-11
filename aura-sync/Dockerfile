# Optional companion to the photo server: periodically pulls photos from an Aura frame account
# and drops them in the photo folder, where the photo server picks them up like any other photos.
#
# The downloader is the reviewed local copy in ./downloader (a trimmed, lightly fixed copy of the
# MIT-licensed https://github.com/meub/aura-frame-downloader, see downloader/NOTICE-LOCAL-CHANGES.md).
# Nothing is fetched from GitHub at build time; the only download is the `requests` library from PyPI.
FROM python:3.12-alpine

LABEL org.opencontainers.image.source="https://github.com/mrjesseking/photoframe-server" \n      org.opencontainers.image.description="Optional Aura frame sync for the Photo Frame server"

RUN pip install --no-cache-dir "requests>=2.31,<3"

COPY downloader /opt/aura-frame-downloader
COPY sync.sh /usr/local/bin/aura-sync
RUN chmod 0755 /usr/local/bin/aura-sync

WORKDIR /opt/aura-frame-downloader
ENV AURA_SCRIPT=/opt/aura-frame-downloader/download-aura-photos.py
USER 65534:65534
ENTRYPOINT ["/usr/local/bin/aura-sync"]
