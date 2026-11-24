FROM docker.io/library/python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
LABEL org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.source="https://github.com/memrok-com/reverie-openwebui" \
      org.opencontainers.image.licenses="Apache-2.0"
WORKDIR /package
COPY adapter.py install.py LICENSE ./
COPY functions/ ./functions/
COPY .stage/engine/reverie-engine /probe/reverie-engine
COPY .stage/engine/LICENSE .stage/engine/NOTICE /package/engine/
RUN groupadd --gid 10001 reverie \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent reverie \
    && mkdir /data && chown 10001:10001 /data \
    && chmod 0555 /probe/reverie-engine
ENV REVERIE_ENGINE=/probe/reverie-engine REVERIE_DATA=/data
USER 10001:10001
ENTRYPOINT ["python3", "-B", "/package/adapter.py"]
