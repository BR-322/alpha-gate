# Cloud Run broker setup

This guide provisions the Cloud Run implementation of Alpha-Gate's
`SandboxExecutor`. It creates three project resources:

- a regional Artifact Registry repository;
- a service account with no project roles; and
- a private Cloud Run service that scales to zero and runs at most one instance.

The broker has already passed the shared local/cloud executor suite. See
[Cloud Run parity v0.1](../reports/cloud_run_parity_v0_1.md) for the tested
revision, image digests, and results. Use this guide to recreate or update that
deployment.

Run the commands from the repository root after authenticating the intended
Cloud Identity account with `gcloud auth login`.

## Set deployment values

Set deployment-specific identifiers in the shell. They are not secrets, but
they should not be hardcoded in the repository.

```bash
export ALPHA_GATE_GCP_PROJECT_ID="your-gcp-project-id"
export ALPHA_GATE_GCP_REGION="us-east1"
export ALPHA_GATE_CLOUD_IDENTITY_EMAIL="your-cloud-identity-email"
export ALPHA_GATE_BROKER_SERVICE_ACCOUNT="alpha-gate-broker@${ALPHA_GATE_GCP_PROJECT_ID}.iam.gserviceaccount.com"
export ALPHA_GATE_ARTIFACT_REGISTRY="${ALPHA_GATE_GCP_REGION}-docker.pkg.dev"
export ALPHA_GATE_BROKER_IMAGE_TAG="replace-with-a-unique-tag"
```

Use a new image tag for each deployment. The parity report records the exact
digest used for the accepted test run.

## 1. Select the gcloud account and project

Create a dedicated gcloud configuration once:

```bash
gcloud config configurations create alpha-gate
```

If it already exists, activate it instead:

```bash
gcloud config configurations activate alpha-gate
```

Set and verify the active account, project, and region:

```bash
gcloud config set account "${ALPHA_GATE_CLOUD_IDENTITY_EMAIL}"
gcloud config set project "${ALPHA_GATE_GCP_PROJECT_ID}"
gcloud config set run/region "${ALPHA_GATE_GCP_REGION}"

gcloud auth list
gcloud config list
```

Do not continue unless the displayed account and project are the intended
Alpha-Gate resources.

## 2. Enable the required APIs

```bash
gcloud services enable \
  artifactregistry.googleapis.com \
  run.googleapis.com
```

This deployment does not require Secret Manager, GKE, a VPC connector, Cloud
SQL, or a downloadable service-account key.

## 3. Create the image repository and runtime identity

Create the Artifact Registry repository:

```bash
gcloud artifacts repositories create alpha-gate \
  --repository-format=docker \
  --location="${ALPHA_GATE_GCP_REGION}" \
  --description="Alpha-Gate sandbox broker images"
```

Create the broker service account:

```bash
gcloud iam service-accounts create alpha-gate-broker \
  --display-name="Alpha-Gate Cloud Run broker"
```

An `ALREADY_EXISTS` response is expected when resuming an existing setup. Do
not grant this service account project roles and do not create a key for it.

## 4. Build and push the broker image

Configure Docker authentication, then build the amd64 image:

```bash
gcloud auth configure-docker "${ALPHA_GATE_ARTIFACT_REGISTRY}"

docker buildx build \
  --platform linux/amd64 \
  --file containers/cloud-run/Dockerfile \
  --tag "${ALPHA_GATE_ARTIFACT_REGISTRY}/${ALPHA_GATE_GCP_PROJECT_ID}/alpha-gate/broker:${ALPHA_GATE_BROKER_IMAGE_TAG}" \
  --push \
  .
```

The allow-listed Docker build context excludes the scorer, market data,
reports, and repository metadata from the image.

## 5. Deploy the private broker

Cloud Run sandboxes are currently a Preview feature exposed through the beta
gcloud component. Install that component once:

```bash
gcloud components install beta --quiet
```

Deploy the service:

```bash
gcloud beta run deploy alpha-gate-broker \
  --image="${ALPHA_GATE_ARTIFACT_REGISTRY}/${ALPHA_GATE_GCP_PROJECT_ID}/alpha-gate/broker:${ALPHA_GATE_BROKER_IMAGE_TAG}" \
  --execution-environment=gen2 \
  --sandbox-launcher \
  --service-account="${ALPHA_GATE_BROKER_SERVICE_ACCOUNT}" \
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

Allow only the selected Cloud Identity user to invoke it:

```bash
gcloud beta run services add-iam-policy-binding alpha-gate-broker \
  --member="user:${ALPHA_GATE_CLOUD_IDENTITY_EMAIL}" \
  --role=roles/run.invoker \
  --region="${ALPHA_GATE_GCP_REGION}"
```

## 6. Run the parity suite

Read the private service URL into the environment and run the cloud-marked
tests:

```bash
export ALPHA_GATE_CLOUD_RUN_URL="$(gcloud run services describe alpha-gate-broker \
  --region="${ALPHA_GATE_GCP_REGION}" \
  --format='value(status.url)')"

uv sync --extra cloud --group dev
uv run pytest -m cloud -v
```

The client obtains a short-lived ID token from the active gcloud identity and
keeps it in memory. The parity suite checks:

- successful execution of the reference candidate;
- candidate runtime failure;
- invalid portfolio weights;
- timeout and subsequent cleanup; and
- output flooding.

Do not use a deployment for candidate experiments unless all parity cases
pass.

## 7. Check cost and remove unused resources

With `--min=0`, the broker has no continuously running instance. Requests use
CPU and memory while active, and stored images incur Artifact Registry cost.
Keep the project-level $50 budget alert enabled and review billing after cloud
tests or experiments.

The following commands delete separate resources. Verify the active project
and region before running any of them.

```bash
gcloud run services delete alpha-gate-broker \
  --region="${ALPHA_GATE_GCP_REGION}"

gcloud artifacts repositories delete alpha-gate \
  --location="${ALPHA_GATE_GCP_REGION}"

gcloud iam service-accounts delete "${ALPHA_GATE_BROKER_SERVICE_ACCOUNT}"
```

Cleanup remains a manual, resource-by-resource operation. The repository does
not provide a project-wide deletion command.
