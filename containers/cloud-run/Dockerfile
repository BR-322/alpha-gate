FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/alpha-gate/app

RUN python -m pip install --no-cache-dir \
    "numpy==2.5.1" \
    "pydantic==2.13.4"

WORKDIR /opt/alpha-gate/app

# Keep the readable sandbox root free of scoring code, market data, and reports.
COPY containers/cloud-run/package/alpha_gate/__init__.py alpha_gate/__init__.py
COPY containers/cloud-run/package/alpha_gate/executors/__init__.py \
    alpha_gate/executors/__init__.py
COPY src/alpha_gate/candidate.py alpha_gate/candidate.py
COPY src/alpha_gate/constants.py alpha_gate/constants.py
COPY src/alpha_gate/protocol.py alpha_gate/protocol.py
COPY src/alpha_gate/executors/base.py alpha_gate/executors/base.py
COPY src/alpha_gate/executors/process.py alpha_gate/executors/process.py
COPY src/alpha_gate/executors/cloud_sandbox.py alpha_gate/executors/cloud_sandbox.py
COPY src/alpha_gate/cloud/__init__.py alpha_gate/cloud/__init__.py
COPY src/alpha_gate/cloud/service.py alpha_gate/cloud/service.py
COPY src/alpha_gate/runtime/worker.py /opt/alpha-gate/worker.py

USER 65532:65532

CMD ["python", "-m", "alpha_gate.cloud.service"]
