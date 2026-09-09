FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv

COPY runner/requirements.txt /srv/runner/requirements.txt
RUN pip install --no-cache-dir -r /srv/runner/requirements.txt

COPY runner/server.py runner/bootstrap.py runner/__init__.py /srv/runner/

RUN useradd --uid 10002 --no-create-home nonroot
USER nonroot

ENV E2_RUNNER_PORT=8001 E2_RUNNER_IMAGE=engineer2-runner:0.1
EXPOSE 8001
CMD ["python", "-m", "runner.server"]
