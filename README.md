# HR Agent Workbench

A recruitment decision-support MVP: turn a job description into confirmed criteria, compare CV evidence, adjust priorities, and prepare three focused interview questions.

Built independently for a Micro Connect HR AI Agent Intern use case. This is not an official Micro Connect product or an endorsement. The company logo remains its owner's property.

## Features

- Editable must-have and bonus criteria, with explicit confirmation.
- Priority ordering that reuses reviewed evidence; semantic changes trigger reanalysis.
- Five workflow roles under one shared HAT: JD understanding, CV extraction, matching, evidence review, and interview questions.
- Original-document citations and highlights alongside concise analysis.
- Separate human review decisions and notes that survive AI reanalysis.
- Configurable role instructions and bounded execution; no automatic hiring or candidate messages.

## Stack

React / TypeScript / Vite, FastAPI / Python, LangGraph, SQLite (local) or PostgreSQL plus private Supabase Storage (hosted), PyMuPDF, OCR and LibreOffice. Model credentials are supplied by the operator. Native PDF extraction precedes OCR. This is a single-workspace MVP, not a multi-tenant ATS.

## Run with Docker

Copy `deploy/production.env.example` to a local ignored `.env` and configure a strong access password, model name and API key. Then:

```sh
docker build -t hr-agent-workbench .
docker volume create hr-agent-data
docker run --rm -p 127.0.0.1:10000:10000 --env-file .env \
  -v hr-agent-data:/data hr-agent-workbench
```

Open http://localhost:10000 and sign in using the configured access credentials. Import your own JD, confirm criteria, and upload your own documents. No personal CVs or precomputed candidate assessments are included in this repository or image.

## Hosting

Use a Docker web service with HTTPS and a persistent disk mounted at `/data`, one application process and one replica. `deploy/start.sh` reads the provider's `PORT`. Authentication protects the app and its APIs/documents; `/health` is the minimal health check. Configure all secrets through the hosting provider. SQLite, document previews and model usage accounting must remain on the persistent disk.

For an ephemeral host such as Render Free, select `HR_STORAGE_BACKEND=supabase` instead. Apply `backend/sql/supabase_bootstrap.sql` to your PostgreSQL project and create a **private** Storage bucket. Configure `HR_DATABASE_URL` using the Supabase **session pooler** on port 5432 with TLS, `HR_SUPABASE_URL`, `HR_SUPABASE_SECRET_KEY`, `HR_STORAGE_BUCKET`, and a disposable `HR_CACHE_DIR`. Database state, diagnostics and usage accounting then reside in PostgreSQL; originals and previews reside in private object storage. Never put the server secret in frontend code. See the environment example for configuration names.

Remote mode still permits only one writer. Stop the old instance before replacing it; zero-downtime rolling deployments are not supported. Free hosting may sleep and free storage projects may pause after inactivity. Cloud persistence and access control must be verified on your actual deployment, independently of local fixture tests.

Deployments interrupt in-flight analysis; interrupted jobs must be retried explicitly. Back up the data disk before upgrades. Model invocation limits are cumulative safety caps, not a monthly billing guarantee. Default live mode does not silently fall back to fixture data.

## Scope and limitations

The workflow produces advice supported by document evidence. Missing evidence remains unknown, even where insufficient coverage lowers the advisory grade. School prestige is not a ranking signal. OCR and model output can contain errors; source links support human verification. Current runtime is a trusted single-user/private-demo deployment, not hardened for anonymous public uploads.

Public source excludes personal documents, local workspaces, credentials, runtime logs, video assets, and private coordination records. Reusable Skill learning and side-by-side candidate comparison are not included as completed features.
