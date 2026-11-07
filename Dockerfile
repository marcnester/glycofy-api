FROM python:3.11-slim-trixie@sha256:db20d8506e2f15b6db39b40b6015ca46bae855dec6dd326d462cfe79ae972893 AS zlib-builder

# Rebuild Debian's security-fixed source against trixie. Only source packages
# come from sid; all compilers/build dependencies remain on stable Debian 13.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential debhelper autoconf automake libtool \
    && printf '%s\n' 'deb-src https://deb.debian.org/debian sid main' > /etc/apt/sources.list.d/zlib-source.list \
    && apt-get update
WORKDIR /zlib-source
RUN apt-get source zlib=1:1.3.dfsg+really1.3.2-4 \
    && echo '7b6903eb019983987b7112eccf90f1703f1c6c0e0cede36564bf611d19ca579d  zlib_1.3.dfsg+really1.3.2.orig.tar.gz' | sha256sum --check \
    && echo 'ee9db06d87b2d810b9edd8485b8b39433b18eb30d93f9e1d40fe8dbd40eda7a1  zlib_1.3.dfsg+really1.3.2-4.debian.tar.xz' | sha256sum --check \
    && cd zlib-* \
    && dpkg-buildpackage --build=binary --no-sign -Pnobiarch,noudeb \
    && mkdir /zlib-packages \
    && cp ../zlib1g_*_$(dpkg --print-architecture).deb /zlib-packages/zlib1g.deb

FROM python:3.11-slim-trixie@sha256:db20d8506e2f15b6db39b40b6015ca46bae855dec6dd326d462cfe79ae972893 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get upgrade --yes \
    && rm -rf /var/lib/apt/lists/*

# Preserve real dpkg ownership/version metadata for the rebuilt patched library.
COPY --from=zlib-builder /zlib-packages/zlib1g.deb /tmp/zlib1g.deb
RUN dpkg --install /tmp/zlib1g.deb \
    && test "$(dpkg-query -W -f='${Version}' zlib1g)" = '1:1.3.dfsg+really1.3.2-4' \
    && rm /tmp/zlib1g.deb

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade \
        pip==26.2.1 \
        setuptools==84.0.0 \
        wheel==0.48.0 \
    && pip install --no-cache-dir --requirement requirements.txt \
    && pip uninstall --yes pip setuptools wheel \
    && python -c "import fastapi, sqlalchemy, uvicorn"

COPY scripts/container/build_rootfs.py /build_rootfs.py
RUN python /build_rootfs.py

FROM scratch AS runtime
COPY --from=builder /runtime/ /
ENV PATH=/usr/local/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
WORKDIR /app

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY ui ./ui
COPY assets ./assets
COPY scripts ./scripts

USER 65532:65532

EXPOSE 10000

CMD ["/usr/local/bin/python", "/app/scripts/container/start.py"]
