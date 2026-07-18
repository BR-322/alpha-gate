FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN python -m pip install --no-cache-dir "numpy==2.5.1"

COPY src/alpha_gate/runtime/worker.py /opt/alpha-gate/worker.py

WORKDIR /candidate
USER 65532:65532
