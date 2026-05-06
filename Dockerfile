FROM node:22-slim AS frontend

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci


FROM python:3.13-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV UV_CACHE_DIR=/tmp/codex-uv-cache
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

COPY --from=frontend /app/node_modules ./node_modules
COPY amber.py amber_web.py ./
COPY librarything_ddrucker_202605061407.json metadata-dmd.db metadata-cad.db ./
COPY web ./web

RUN chmod +x /app/amber.py /app/amber_web.py
RUN uv run --script ./amber_web.py --help >/dev/null

EXPOSE 2380

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:2380/healthz', timeout=3).read()"

CMD ["uv", "run", "--script", "./amber_web.py", "--host", "0.0.0.0", "--port", "2380"]
