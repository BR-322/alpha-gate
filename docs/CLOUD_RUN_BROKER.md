# Cloud Run broker provisioning

This is the manual and CLI boundary for the Alpha-Gate Cloud Run sandbox
parity gate. Run these commands only after `gcloud auth login` succeeds for the
Cloud Identity account. They create one private, scale-to-zero service and a
regional Artifact Registry repository.

The defaults below use project `YOUR_GCP_PROJECT_ID`, region `us-east1`,
and a one-instance cap. The runtime service account receives no project roles.

## 1. Set a dedicated gcloud configuration

```bash
gcloud config configurations create alpha-gate
gcloud config set account YOUR_CLOUD_IDENTITY_EMAIL
gcloud config set project YOUR_GCP_PROJECT_ID
gcloud config set run/region us-east1
```

If the configuration already exists, activate it instead:

```bash
gcloud config configurations activate alpha-gate
```

## 2. Enable the two required APIs

```bash
gcloud services enable \
  artifactregistry.googleapis.com \
  run.googleapis.com
```

No Secret Manager, GKE, VPC connector, Cloud SQL, or service-account key is
needed for this gate.

## 3. Create the image repository and zero-role runtime identity

```bash
gcloud artifacts repositories create alpha-gate \
  --repository-format=docker \
  --location=us-east1 \
  --description="Alpha-Gate sandbox broker images"

gcloud iam service-accounts create alpha-gate-broker \
  --display-name="Alpha-Gate Cloud Run broker"
```

An `ALREADY_EXISTS` response is safe when resuming setup. Do not grant the
runtime identity project roles and do not create or download a key.

## 4. Build and push the minimal amd64 image

```bash
gcloud auth configure-docker us-east1-docker.pkg.dev

docker buildx build \
  --platform linux/amd64 \
  --file containers/cloud-run/Dockerfile \
  --tag us-east1-docker.pkg.dev/YOUR_GCP_PROJECT_ID/alpha-gate/broker:parity-v5 \
  --push \
  .
```

The Docker build context is allow-listed. The broker image contains no scorer,
market data, reports, or repository metadata.

## 5. Deploy one private, scale-to-zero broker

The sandbox launcher is currently a beta gcloud flag. Install that component
once with `gcloud components install beta --quiet`.

```bash
gcloud beta run deploy alpha-gate-broker \
  --image=us-east1-docker.pkg.dev/YOUR_GCP_PROJECT_ID/alpha-gate/broker:parity-v5 \
  --execution-environment=gen2 \
  --sandbox-launcher \
  --service-account=YOUR_BROKER_SERVICE_ACCOUNT_EMAIL \
  --no-allow-unauthenticated \
  --concurrency=1 \
  --cpu=1 \
  --memory=512Mi \
  --cpu-throttling \
  --timeout=360 \
  --min=0 \
  --max=1 \
  --set-env-vars=ALPHA_GATE_CPU_CEILING=1,ALPHA_GATE_MEMORY_CEILING_MB=512
```

Grant only the Cloud Identity user permission to call this service:

```bash
gcloud beta run services add-iam-policy-binding alpha-gate-broker \
  --member=user:YOUR_CLOUD_IDENTITY_EMAIL \
  --role=roles/run.invoker \
  --region=us-east1
```

## 6. Run the live parity gate

```bash
export ALPHA_GATE_CLOUD_RUN_URL="$(gcloud run services describe alpha-gate-broker \
  --region=us-east1 \
  --format='value(status.url)')"

uv sync --extra cloud --group dev
uv run pytest -m cloud -v
```

The client mints a short-lived ID token from the active gcloud identity and
caches it only in memory. A passing gate must cover the reference candidate,
candidate runtime failure, invalid weights, timeout, and output flooding.

## 7. Inspect cost and tear down if the preview is unsuitable

Cloud Run has no minimum instance charge with `--min=0`; requests consume CPU
and memory while active. Keep the existing $50 project budget alert in place
and inspect Cloud Run plus Artifact Registry costs after the parity run.

The service and image repository can be removed independently if needed:

```bash
gcloud run services delete alpha-gate-broker --region=us-east1
gcloud artifacts repositories delete alpha-gate --location=us-east1
gcloud iam service-accounts delete \
  YOUR_BROKER_SERVICE_ACCOUNT_EMAIL
```

Deletion is intentionally manual; no project-wide cleanup command belongs in
the repository.
