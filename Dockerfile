FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir .

RUN groupadd --gid 1001 ostrakon \
    && useradd --uid 1000 --gid 1001 --no-create-home --shell /usr/sbin/nologin ostrakon \
    && mkdir -p /data \
    && chown -R ostrakon:ostrakon /data

USER ostrakon

CMD ["ostrakon"]
