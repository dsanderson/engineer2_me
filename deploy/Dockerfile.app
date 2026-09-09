FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /srv
COPY pyproject.toml uv.lock* /srv/
RUN uv sync --no-dev --frozen 2>/dev/null || uv sync --no-dev

COPY main.py /srv/
COPY app /srv/app
COPY skills /srv/skills

RUN useradd --uid 10001 --create-home engineer2 && mkdir -p /data && chown -R engineer2 /data /srv
USER engineer2

ENV PATH="/srv/.venv/bin:$PATH" E2_DATA_DIR=/data E2_HOST=0.0.0.0 E2_PORT=8000
EXPOSE 8000
CMD ["python", "main.py"]
