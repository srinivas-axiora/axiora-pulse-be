# Axiora Pulse — Backend API

> **Core AI Orchestration Engine** — Converts founder ideas into structured validation journeys using MCP, Skills, and Agentic Workflows.

Built with **FastAPI** · **PostgreSQL** · **SQLAlchemy (async)** · **Alembic** · **Multi-provider LLM support**

---

## Table of Contents

- [Overview](#overview)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [Environment Variables](#environment-variables)
- [Running the Server](#running-the-server)
- [Database Migrations](#database-migrations)
- [API Reference](#api-reference)
  - [Auth](#auth-endpoints)
  - [Workspaces](#workspace-endpoints)
  - [Surveys](#survey-endpoints)
  - [Questionnaire](#questionnaire-endpoints)
  - [Interactive Questionnaire Admin](#interactive-questionnaire-admin-endpoints)
  - [Admin](#admin-endpoints)
  - [Profile](#profile-endpoints)
  - [Token Analytics](#token-analytics-endpoints)
  - [Contact](#contact-endpoint)
  - [Billing](#billing-endpoints)
  - [Orchestration](#orchestration-endpoints)
  - [Health & Root](#health--root)
- [LLM Providers](#llm-providers)
- [Skills System](#skills-system)
- [Testing & Quality](#testing--quality)
- [Rate Limiting](#rate-limiting)
- [Security](#security)

---

## Overview

Axiora Pulse is an AI-powered platform that helps founders validate business ideas through structured mentor conversations, market research, and automated agent workflows. This repository contains the FastAPI backend that powers:

- **AI Mentor Chat** — Guided idea-validation conversations inside workspaces
- **Agentic Orchestration** — Multi-agent idea validation and market research pipelines, with real-time research-trace streaming (SSE) and post-link survey intelligence
- **Interactive Questionnaire** — Admin-managed questionnaire templates and user answer submission
- **JWT Authentication** — Secure register/login with OTP-based MFA plus **Google Single Sign-On**, and branded transactional emails (welcome, password-reset confirmation) sent asynchronously via a background job dispatcher
- **Workspace Management** — Persistent workspaces scoped to each user, including soft-delete/restore and file/image/PDF/doc/link attachments (stored in S3), plus PDF report and **Certificate of Completion** generation
- **Surveys** — Create, publish, and collect public responses to workspace-scoped surveys, with CSV export and automatic post-link response intelligence analysis
- **Billing** — Razorpay-powered subscriptions with a secure-by-default payment gate (subscription-enforced feature flag) and webhook-confirmed entitlement
- **Contact Form** — Public "Get in Touch" submissions forwarded to the support inbox as a background email job
- **Token Analytics** — Per-user/workspace/platform LLM token consumption and cost tracking
- **Real-Time Web Search & Scraping** — DuckDuckGo/Tavily search and webpage extraction feeding agents during research
- **Admin** — Restricted endpoints for user listing, cross-user survey analytics, user-growth stats, and questionnaire management (`role=admin`)

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web Framework | FastAPI ≥ 0.115 |
| ASGI Server | Uvicorn |
| Database | PostgreSQL (via asyncpg) |
| ORM | SQLAlchemy 2.0 (async) |
| Migrations | Alembic |
| Auth | python-jose JWT (HS256), `google-auth` for Google SSO |
| LLM Providers | OpenAI, HuggingFace (Anthropic & Azure OpenAI are Phase 2+ stubs) |
| Rate Limiting | SlowAPI |
| Validation | Pydantic v2 |
| Email | SMTP (OTP dispatch + branded transactional emails via a background job dispatcher) |
| Storage | AWS S3 (`boto3`) — workspace attachments and generated report assets |
| Billing | Razorpay (`razorpay`) — subscriptions, webhooks, payment gate |
| File Processing | `pdfplumber`, `PyMuPDF`, `python-docx`, `beautifulsoup4` (attachment parsing); `reportlab` (PDF report generation); `fitz`/PyMuPDF (certificates) |
| Web Research | `ddgs` / `duckduckgo-search` (web search), `httpx` + `beautifulsoup4` (web scraping) |

---

## Project Structure

```
backend/
├── main.py                     # FastAPI app entry point, middleware, routers
├── alembic/
│   └── versions/               # Database migration scripts
├── deploy/
│   ├── README.md               # Dev environment runbook
│   ├── docker-compose.yml      # Local/dev container wiring
│   ├── Caddyfile               # Caddy reverse proxy configuration
│   └── setup.sh                # Box bootstrap script
├── tests/
│   ├── conftest.py             # Async DB/client fixtures and overrides
│   ├── test_auth.py            # Auth, OTP, reset, and dependency coverage
│   ├── test_admin.py           # Admin route coverage
│   ├── test_admin_analytics.py # Admin user-growth / cross-user analytics
│   ├── test_profile.py         # /api/auth/me, /api/users/me coverage
│   ├── test_email_templates.py # Transactional email template rendering
│   ├── test_background_jobs.py # Email job dispatcher (retry/backoff) coverage
│   ├── test_surveys.py         # Survey CRUD, public submission, export
│   ├── test_questionnaire.py   # Questionnaire validation and admin routes
│   ├── test_workspaces.py      # Workspace CRUD and ownership coverage
│   ├── test_workspace_subresources.py # Chat, state, reset, report export
│   ├── test_workspace_attachments.py  # Attachment upload/list/delete
│   ├── test_attachment_processing.py / test_attachment_processor_unit.py # PDF/DOCX/link/image parsing
│   ├── test_orchestration.py   # Agentic orchestration endpoint
│   ├── test_billing.py         # Razorpay billing service + API
│   ├── test_token_tracking.py  # LLM token usage & cost tracking
│   ├── test_contact.py         # Contact form endpoint + job dispatch
│   ├── test_certificate.py     # Certificate of Completion PDF generation
│   ├── test_s3_storage_service.py / test_workspace_attachment_service.py # Storage backend
│   ├── test_web_tools.py       # Web search / scraper (live) + MCP web tools
│   ├── test_web_search_service_unit.py / test_web_scraper_service_unit.py # Web services (mocked)
│   ├── test_research_trace_service.py # SSE research-trace streaming
│   ├── test_survey_service_unit.py # Agent-result → survey auto-sync helper
│   ├── test_user_details_service.py # Extended profile (user_details) service
│   ├── test_base_agent.py, test_idea_validation_agent.py,
│   │   test_market_research_agent.py, test_survey_intelligence_agent.py # Per-agent unit tests
│   └── test_database_constraints.py # Schema and integrity checks
├── test_admin_script.py        # Manual admin login smoke test
├── test_llm_connectivity.py, test_llm_streaming.py,
│   test_mcp_servers.py, test_full_pipeline_up_to_survey_agent.py,
│   test_survey_agent_validation.py, test_analyze_endpoint.py,
│   test_email.py, test_market_research_agent_realtime.py  # Manual/dev smoke-test scripts (not part of the pytest suite)
├── alembic.ini                 # Alembic configuration
├── requirements.txt
├── .coveragerc                 # Coverage scope/omit rules
├── Dockerfile                  # Container build
├── scratch/                    # Dev/scratch scripts
├── uploads/                    # Local fallback storage for attachments/avatars
└── app/
    ├── api/
    │   ├── billing.py          # Unversioned /api/billing/* (Razorpay subscriptions)
    │   ├── profile.py          # Unversioned /api/auth/me, /api/users/me + avatar/details sub-resources
    │   └── v1/
    │       ├── auth.py         # Auth endpoints (register, login, OTP, password, refresh, logout, Google SSO)
    │       ├── admin.py        # Admin-only endpoints (role=admin), e.g. user listing
    │       ├── workspace.py    # Workspace CRUD + AI Mentor sub-resources + attachments + certificate
    │       ├── surveys.py      # Survey CRUD, public submission, and export
    │       ├── questionnaire.py # Public questionnaire routes
    │       ├── interactive_questionnaire.py # Admin questionnaire routes
    │       ├── orchestration.py # Agentic orchestration endpoint
    │       ├── analytics.py    # Token-usage analytics (/api/v1/analytics/tokens/me, /admin/tokens)
    │       ├── contact.py      # Public contact form (POST /api/v1/contact)
    │       ├── mentor.py       # Deprecated stub; workspace routes replaced these
    │       ├── agents.py       # Reserved for Phase 2 (unmounted stub)
    │       └── reports.py      # Deprecated stub; workspace routes replaced these
    ├── core/
    │   ├── dependencies.py     # get_current_user JWT dependency
    │   ├── security.py         # Token creation, password hashing, OTP utils
    │   ├── limiter.py          # SlowAPI rate limiter setup
    │   ├── logging.py          # Structured logging configuration
    │   ├── timezone.py         # IST date/time helpers
    │   ├── google_auth.py      # Google SSO / OAuth helpers
    │   └── config.py           # Deprecated stub, kept for compatibility
    ├── db/
    │   ├── database.py         # Async DB engine, session factory, migration runner
    │   └── models.py           # SQLAlchemy ORM models (User, Workspace, Survey, Billing, Tokens, ...)
    ├── models/
    │   ├── auth_models.py      # Pydantic request/response models for auth
    │   ├── admin_models.py     # Admin request/response models
    │   ├── questionnaire_models.py # Questionnaire request/response models
    │   ├── workspace_models.py # Pydantic request/response models for workspaces
    │   ├── survey_models.py    # Survey request/response models
    │   ├── orchestration_models.py
    │   ├── agent_models.py
    │   ├── skill_models.py
    │   ├── billing_models.py   # Plans / subscriptions / SubscribeOut
    │   ├── contact_models.py   # Contact form request/envelope
    │   ├── token_models.py     # Token analytics response models
    │   └── user_details_models.py # Extended profile request/response models
    ├── services/
    │   ├── auth_service.py     # Registration, OTP verification, login logic
    │   ├── admin_service.py    # Admin user-listing logic
    │   ├── questionnaire_service.py # Questionnaire CRUD and answer persistence
    │   ├── workspace_service.py # Workspace CRUD and mentor session logic
    │   ├── workspace_attachment_service.py # Attachment upload/list/delete orchestration
    │   ├── attachment_processor.py # PDF/DOCX/link/image content extraction
    │   ├── s3_storage_service.py # AWS S3 storage backend (falls back to local disk without AWS creds)
    │   ├── survey_service.py   # Survey CRUD, public link generation, response export, auto-sync
    │   ├── mentor_service.py   # AI Mentor conversation engine
    │   ├── report_service.py   # PDF/Doc report generation
    │   ├── certificate_service.py # Certificate of Completion PDF generator
    │   ├── research_trace_service.py # SSE research-query/source tracing (orchestration streaming)
    │   ├── token_tracking_service.py # LLM token-usage recording + per-user/workspace/platform analytics
    │   ├── user_details_service.py # Extended user profile (1:1 user_details) operations
    │   ├── billing_service.py  # Razorpay subscription persistence, webhook processing, entitlement gate
    │   ├── razorpay_service.py # Thin Razorpay SDK wrapper (keys/secret/webhooks)
    │   ├── contact_service.py  # Contact-form submission → background email job
    │   ├── web_search_service.py # Real-time web search (DuckDuckGo / Tavily)
    │   ├── web_scraper_service.py # Webpage fetch + clean text extraction
    │   ├── email_service.py    # SMTP email delivery (OTP + transactional emails)
    │   ├── email_templates.py  # Shared HTML layout/components for transactional emails
    │   └── otp_dispatcher.py   # OTP routing (email / SMS)
    ├── templates/               # Static email assets (logo images) + certificate template/fonts
    ├── agents/                 # AI agent implementations
    ├── orchestration/          # Multi-agent workflow orchestrator (planner, context_builder, result_aggregator, validation_engine)
    ├── skills/                 # Skill Markdown definitions + registry
    ├── llm/                    # LLM provider abstraction layer
    ├── mcp/                    # Model Context Protocol integration (mcp_host.py, tool_registry.py, tools/)
    ├── guardrails/             # Input/output safety guards (output_guardrails; input/scope/financial are Phase 2 stubs)
    └── workers/
        └── background_jobs.py  # Fire-and-forget async email job dispatcher with retry/backoff
```

---

## Getting Started

### Prerequisites

- Python 3.11+
- PostgreSQL 14+
- A virtual environment tool (`venv` or `conda`)

### Installation

```bash
# 1. Clone the repository
git clone <repo-url>
cd backend

# 2. Create and activate virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS / Linux

# 3. Install dependencies
pip install -r requirements.txt

```

---

## Environment Variables

Create a `.env` file in the `backend/` directory. **Never commit this file.**

Below is a reference of all supported variables with descriptions:

### Application

| Variable | Default | Description |
|---|---|---|
| `APP_NAME` | `Axiora Pulse AI Engine` | Display name shown in logs and API docs |
| `APP_VERSION` | `1.0.0` | Application version |
| `DEBUG` | `true` | Enables Swagger UI (`/docs`), verbose logs, and permissive CORS. Set to `false` in production. |

### Database

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | ✅ | Async PostgreSQL connection string. Format: `postgresql+asyncpg://user:password@host:port/dbname` |

### Security & JWT

| Variable | Required | Description |
|---|---|---|
| `JWT_SECRET_KEY` | ✅ | Secret key used to sign JWT tokens. **Must be changed in production.** |
| `JWT_ALGORITHM` | ✅ | JWT signing algorithm (e.g. `HS256`) — no code default, must be set or auth will fail to start |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | ✅ | Access token lifetime in minutes — no code default, must be set |
| `OTP_EXPIRE_MINUTES` | `10` (email service only) | OTP lifetime in minutes. Displayed in OTP emails via a default of `10`; the security-layer OTP expiry calculation itself has no default and must be set. |

> Refresh-token lifetime is currently hardcoded to 7 days in `app/core/security.py` — there is no `REFRESH_TOKEN_EXPIRE_DAYS` environment variable read anywhere in the code.

### LLM Providers

| Variable | Required | Description |
|---|---|---|
| `DEFAULT_PROVIDER` | `huggingface` | Active LLM provider: `openai`, `anthropic`, `huggingface`, or `azure_openai` |
| `DEFAULT_MODEL` | `meta-llama/Llama-3.1-8B-Instruct` | Fallback model if no provider-specific model is set |
| `HF_TOKEN` | If using HuggingFace | HuggingFace API token |
| `HF_MODEL` | — | HuggingFace model ID (e.g. `meta-llama/Llama-3.1-8B-Instruct`) |
| `HF_BASE_URL` | — | HuggingFace router base URL |
| `HF_TIMEOUT` | `120` | HuggingFace request timeout in seconds |
| `HF_MAX_RETRIES` | `2` | HuggingFace retry count |
| `OPENAI_API_KEY` | If using OpenAI | OpenAI API key |
| `OPENAI_MODEL` | `gpt-4o-mini` | OpenAI model to use |
| `OPENAI_BASE_URL` | — | Optional OpenAI-compatible base URL |
| `OPENAI_TIMEOUT` | `60` | OpenAI request timeout in seconds |
| `OPENAI_MAX_RETRIES` | `2` | OpenAI retry count |
| `ANTHROPIC_API_KEY` | If using Anthropic | Anthropic API key |
| `ANTHROPIC_MODEL` | `claude-3-5-sonnet-20241022` | Anthropic model to use |
| `AZURE_OPENAI_API_KEY` | If using Azure | Azure OpenAI API key |
| `AZURE_OPENAI_ENDPOINT` | If using Azure | Azure OpenAI endpoint URL |
| `AZURE_OPENAI_MODEL` | `gpt-4o` | Azure deployment name |

### Email (OTP Dispatch)

| Variable | Required | Description |
|---|---|---|
| `SMTP_HOST` | ✅ | SMTP server hostname |
| `SMTP_PORT` | ✅ | SMTP server port (usually `587` for TLS) |
| `SMTP_USER` | ✅ | SMTP login username (sender email) |
| `SMTP_PASSWORD` | ✅ | SMTP login password or app password |
| `SMTP_FROM_EMAIL` | — | Envelope sender address used in outgoing mail |
| `SMTP_FROM_NAME` | `Axiora Pulse` | Display name for outgoing emails |
| `SUPPORT_EMAIL` | `no.reply@axiorapulse.com` | Support contact address shown in transactional emails (e.g. "didn't request this?" notices) |
| `DASHBOARD_LOGIN_URL` | `https://qa.axiorapulse.com/login` | Frontend login/dashboard URL linked from the "Go to Dashboard" button in the welcome email |
| `EMAIL_LOGO_LIGHT_URL` | Cloudinary-hosted default | Hosted URL of the light-background Axiora Pulse logo used in transactional emails |
| `EMAIL_LOGO_DARK_URL` | Cloudinary-hosted default | Hosted URL of the dark-background Axiora Pulse logo, swapped in for dark-mode-aware email clients |
| `EMAIL_TIMEZONE` | `Asia/Kolkata` | IANA timezone used to render timestamps (e.g. password-changed time) shown in transactional emails |

### Storage (S3 — Workspace Attachments & Report Assets)

| Variable | Required | Description |
|---|---|---|
| `AWS_ACCESS_KEY_ID` | If using S3 | AWS access key. Without AWS credentials configured, `s3_storage_service` falls back to writing files locally under `uploads/` (used automatically in local/dev/test environments). |
| `AWS_SECRET_ACCESS_KEY` | If using S3 | AWS secret key |
| `AWS_REGION` | `us-east-1` | AWS region for the attachments bucket |
| `AWS_S3_BUCKET_NAME` | `axiora-pulse-attachments` | Bucket used for workspace attachment uploads |
| `S3_ENDPOINT_URL` | — | Optional custom S3-compatible endpoint (e.g. MinIO, LocalStack) |
| `AWS_ASSETS_BUCKET_NAME` | `axiora-assets` | Bucket used for generated report assets |
| `AWS_ASSETS_REGION` | `ap-south-1` | Region for the assets bucket |

### PDF Report Rendering

| Variable | Required | Description |
|---|---|---|
| `AXIORA_REPORT_BANNER_FONT_REGULAR` | — | Path to the regular-weight font used in generated PDF report banners |
| `AXIORA_REPORT_BANNER_FONT_BOLD` | — | Path to the bold-weight font used in generated PDF report banners |
| `AXIORA_REPORT_BANNER_VENTURE_FONT_REGULAR` | — | Regular-weight font for the venture-report banner variant |
| `AXIORA_REPORT_BANNER_VENTURE_FONT_BOLD` | — | Bold-weight font for the venture-report banner variant |
| `AXIORA_REPORT_TEMPLATE_PATH` | — | Override path to the PDF report template |

> Font-path defaults in `app/services/report_service.py` assume a Windows font directory (`WINDIR`, i.e. `C:\Windows\Fonts`). On non-Windows deploy targets, set these explicitly.

### Billing (Razorpay)

| Variable | Required | Description |
|---|---|---|
| `RAZORPAY_KEY_ID` | If using billing | Razorpay public key id (`rzp_test_…` in Test mode, `rzp_live_…` in Live) |
| `RAZORPAY_KEY_SECRET` | If using billing | Razorpay secret key — never sent to the client |
| `RAZORPAY_WEBHOOK_SECRET` | If using billing | Shared secret configured on the dashboard webhook for signature verification |
| `RAZORPAY_TOTAL_COUNT` | `120` | Number of billing cycles Razorpay attempts before a subscription completes (120 ≈ 10 years monthly) |
| `SUBSCRIPTION_ENFORCED` | `true` | Gates subscription-only features. Set to `false` **only** for local development to bypass the payment gate. Defaults to enforcing (a malformed/missing value never silently opens the gate). |

### Public URLs

| Variable | Default | Description |
|---|---|---|
| `PUBLIC_APP_URL` | — | Canonical public frontend origin. Used to build public survey links, admin survey links, and server-proxied avatar URLs. |
| `BACKEND_URL` | — | Backend origin used as a fallback when building proxied avatar URLs. |

### Web Search & Scraping

| Variable | Default | Description |
|---|---|---|
| `WEB_SEARCH_PROVIDER` | `duckduckgo` | Active real-time search provider: `duckduckgo` or `tavily` |
| `MAX_SEARCH_RESULTS` | `5` | Default max number of search results returned per query |
| `TAVILY_API_KEY` | — | Tavily API key (only required if `WEB_SEARCH_PROVIDER=tavily`) |

### Google SSO

| Variable | Required | Description |
|---|---|---|
| `GOOGLE_CLIENT_ID` | If using Google SSO | Google OAuth client id used to verify Google-issued id_tokens during SSO login |

### Contact Form

| Variable | Default | Description |
|---|---|---|
| `CONTACT_EMAIL` | — | Support inbox recipient for public contact-form submissions |

### CORS

| Variable | Default | Description |
|---|---|---|
| `ALLOWED_ORIGINS` | `*` (in DEBUG) | Comma-separated list of allowed frontend origins. Example: `https://app.axiorapulse.com,https://staging.axiorapulse.com` |

---

## Running the Server

```bash
# Development (with hot reload)
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Production
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```


| URL | Description |
|---|---|
| `http://localhost:8000/` | Root — lists available routes |
| `http://localhost:8000/docs` | Swagger UI (DEBUG mode only) |
| `http://localhost:8000/redoc` | ReDoc (DEBUG mode only) |
| `http://localhost:8000/health` | Health check |

---

## Database Migrations

Migrations run automatically on server startup via `alembic upgrade head`.

To manage migrations manually:

```bash
# Apply all pending migrations
alembic upgrade head

# Create a new migration (after editing ORM models)
alembic revision --autogenerate -m "describe your change"

# Roll back one migration
alembic downgrade -1

# View current revision
alembic current
```

---

## API Reference

Most endpoints are prefixed with `/api/v1`. The exception is the Profile router (`app/api/profile.py`), which is mounted unversioned under `/api` to match the existing SPA contract — see [Profile Endpoints](#profile-endpoints). Authentication uses **JWT Bearer tokens**.

### Auth Endpoints

| Method | Route | Auth | Description |
|---|---|---|---|
| `POST` | `/api/v1/auth/register` | ❌ | Create a new account. Sends a 6-digit OTP to the provided email. |
| `POST` | `/api/v1/auth/verifyOTP` | ❌ | Verify the registration OTP. Returns `access_token` + `refresh_token`. On success, also fires a welcome email in the background. |
| `POST` | `/api/v1/auth/resendOTP` | ❌ | Resend a new registration OTP (invalidates the previous one). |
| `POST` | `/api/v1/auth/login` | ❌ | Validate credentials and dispatch a login OTP. |
| `POST` | `/api/v1/auth/google` | ❌ | Sign in or register with Google (verify an ID token; no OTP required). |
| `POST` | `/api/v1/auth/verify-login` | ❌ | Verify the login OTP. Returns `access_token` + `refresh_token`. |
| `POST` | `/api/v1/auth/admin/login` | ❌ | Admin-specific login (validates `role="admin"`). |
| `POST` | `/api/v1/auth/refresh` | ❌ | Rotate a valid refresh token for a new access/refresh token pair. |
| `POST` | `/api/v1/auth/logout` | ✅ | Revoke all active refresh sessions for the authenticated user. |
| `POST` | `/api/v1/auth/forgot-password/request` | ❌ | Request a password reset OTP. |
| `POST` | `/api/v1/auth/forgot-password/verify` | ❌ | Verify the reset OTP. Returns a short-lived `reset_token`. |
| `POST` | `/api/v1/auth/forgot-password/reset` | ❌ | Set a new password using the `reset_token`. On success, fires a password-changed confirmation email in the background. |
| `POST` | `/api/v1/auth/change-password` | ✅ | Change password for the authenticated user. Revokes all existing sessions and fires a password-changed confirmation email in the background. |

Registration and password-change confirmation emails are dispatched asynchronously via `app/workers/background_jobs.py` — delivery is retried on transient failure and never blocks or fails the API request. See `app/services/email_templates.py` / `email_service.py` for the templates.

#### Token Response Shape (register & login)
```json
{
  "status": "success",
  "message": "...",
  "access_token": "<JWT>",
  "refresh_token": "<JWT>",
  "token_type": "bearer",
  "expires_in_minutes": 60
}
```

---

### Workspace Endpoints

All workspace routes require a valid **JWT Bearer token**.

| Method | Route | Description |
|---|---|---|
| `POST` | `/api/v1/workspaces` | Create a new workspace (returns `201 Created`) |
| `GET` | `/api/v1/workspaces` | List all workspaces for the current user |
| `GET` | `/api/v1/workspaces/user/{user_id}` | List all workspaces for a given user |
| `GET` | `/api/v1/workspaces/{workspace_id}` | Get a single workspace by ID |
| `PUT` | `/api/v1/workspaces/{workspace_id}` | Update workspace `name` and/or `description` |
| `DELETE` | `/api/v1/workspaces/{workspace_id}` | Soft-delete a workspace (returns `204 No Content`) |
| `DELETE` | `/api/v1/workspaces/{workspace_id}/permanent` | Permanently (hard) delete a workspace |
| `PATCH` | `/api/v1/workspaces/{workspace_id}/restore` | Restore a soft-deleted workspace |
| `POST` | `/api/v1/workspaces/{workspace_id}/chat` | Send a message to the AI Mentor inside a workspace |
| `GET` | `/api/v1/workspaces/{workspace_id}/state` | Get full dialogue history and validation state |
| `POST` | `/api/v1/workspaces/{workspace_id}/reset` | Reset mentor conversation for a workspace |
| `PUT` | `/api/v1/workspaces/{workspace_id}/survey/questions` | Override the survey questions generated for a workspace |
| `GET` | `/api/v1/workspaces/{workspace_id}/reports/{agent_name}` | Download a PDF/Doc agent report |
| `POST` | `/api/v1/workspaces/{workspace_id}/reports/export` | Export an agent report via POST body |
| `POST` | `/api/v1/workspaces/{workspace_id}/attachments` | Upload a file/image/PDF/doc/link attachment (returns `201 Created`) |
| `GET` | `/api/v1/workspaces/{workspace_id}/attachments` | List attachments for a workspace |
| `GET` | `/api/v1/workspaces/{workspace_id}/attachments/{attachment_id}` | Get a single attachment |
| `DELETE` | `/api/v1/workspaces/{workspace_id}/attachments/{attachment_id}` | Delete an attachment |
| `GET` | `/api/v1/workspaces/{workspace_id}/certificate` | Download a Certificate of Completion PDF (requires a validated workspace) |

These workspace sub-resources replace the deprecated `/api/v1/mentor/*` and `/api/v1/reports/*` routes. Attachments are stored via `app/services/s3_storage_service.py` (S3, falling back to local disk under `uploads/` when AWS credentials are not configured) and parsed via `app/services/attachment_processor.py`.

#### Create Workspace Request
```json
{
  "name": "My Startup Idea",          // Required, 1–100 characters
  "description": "Optional context"   // Optional
}
```

#### Workspace Response
```json
{
  "id": 1,
  "user_id": 42,
  "name": "My Startup Idea",
  "description": "Optional context",
  "state": "GATHERING_INFO",
  "created_at": "2026-07-27T06:00:00Z",
  "updated_at": "2026-07-27T06:00:00Z"
}
```

---

### Survey Endpoints

All routes except the two `public` routes require a valid **JWT Bearer token**.

| Method | Route | Auth | Description |
|---|---|---|---|
| `POST` | `/api/v1/surveys` | ✅ | Create a survey |
| `GET` | `/api/v1/surveys` | ✅ | List surveys for the current user |
| `GET` | `/api/v1/surveys/workspace/{workspace_id}` | ✅ | List surveys for a workspace |
| `GET` | `/api/v1/surveys/{survey_id}` | ✅ | Get a single survey |
| `PUT` | `/api/v1/surveys/{survey_id}` | ✅ | Update a survey |
| `DELETE` | `/api/v1/surveys/{survey_id}` | ✅ | Delete a survey (returns `204 No Content`) |
| `GET` | `/api/v1/surveys/{survey_id}/export` | ✅ | Export survey responses (CSV) |
| `GET` | `/api/v1/surveys/{survey_id}/responses` | ✅ | List raw survey responses |
| `GET` | `/api/v1/surveys/public/{token}` | ❌ | Public survey view, for respondents |
| `POST` | `/api/v1/surveys/public/{token}/submit` | ❌ | Public survey submission (returns `201 Created`) |

Public survey links are built from `PUBLIC_APP_URL` (see [Environment Variables](#environment-variables)).

The two public routes are keyed by `Survey.public_token` — an opaque, randomly generated identifier (`uuid.uuid4().hex`, unique-indexed) — rather than the internal sequential `survey_id` used everywhere else in this table. This is deliberate: those two routes are unauthenticated by design (external respondents have no account), so keying them off a sequential integer would let anyone enumerate `/public/1`, `/public/2`, ... and view or submit responses to surveys never shared with them. The owner-facing routes above stay on the internal `id` since they're already protected by JWT auth + ownership checks.

---

### Questionnaire Endpoints

All questionnaire routes require a valid **JWT Bearer token**.

| Method | Route | Description |
|---|---|---|
| `GET` | `/api/v1/questionnaire/questions` | List active questionnaire questions in ID order |
| `POST` | `/api/v1/questionnaire/submit-answers` | Submit or update questionnaire answers for the current user |

#### Questionnaire behavior
- Required questions must be present in the submission payload.
- Choice questions only accept values that exist in the question's answer list.
- Single-choice questions accept only one selected answer.
- Submitted answer strings are trimmed and empty values are discarded.
- Existing answers are updated instead of duplicated for the same user/question pair.

---

### Interactive Questionnaire Admin Endpoints

All admin questionnaire routes require a valid **JWT Bearer token** with `role=admin`.

| Method | Route | Description |
|---|---|---|
| `POST` | `/api/v1/admin/questionnaire/create-question` | Create an interactive questionnaire question |
| `GET` | `/api/v1/admin/questionnaire/questions` | List all questionnaire questions |
| `POST` | `/api/v1/admin/questionnaire/submit-answers` | Submit questionnaire answers through the admin namespace |
| `DELETE` | `/api/v1/admin/questionnaire/delete-question/{question_id}` | Delete a questionnaire question |

#### Admin questionnaire behavior
- Choice-based questions require at least two answer options.
- Admin create/delete operations are restricted to users with `role="admin"`.
- The admin routes reuse the same validation and answer persistence rules as the public questionnaire routes.

---

### Admin Endpoints

Requires a valid **JWT Bearer token** with `role=admin`. Distinct from the [Interactive Questionnaire Admin](#interactive-questionnaire-admin-endpoints) router — both happen to share the `/api/v1/admin/*` prefix but live in separate files (`app/api/v1/admin.py` vs. `app/api/v1/interactive_questionnaire.py`).

| Method | Route | Description |
|---|---|---|
| `GET` | `/api/v1/admin/users` | List all registered users |
| `GET` | `/api/v1/admin/users/surveys` | List surveys across users, optionally filtered by `user_id`; each row includes `survey_link` |
| `GET` | `/api/v1/admin/users/{user_id}/survey-summary` | Get one user's survey/response totals and per-survey links |
| `GET` | `/api/v1/admin/surveys/{survey_id}/responses` | List collected responses for a survey with pagination/search |
| `GET` | `/api/v1/admin/surveys/{survey_id}/responses/{response_id}` | Get one collected response with enriched answer preview |
| `GET` | `/api/v1/admin/stats/user-growth` | Return new-user counts bucketed by month or year |
| `PATCH` | `/api/v1/admin/user-details/{user_id}/status` | Set a user's profile status (Active/Inactive/Suspended) |

### Profile Endpoints

Mounted **unversioned** under `/api` (not `/api/v1`) to match the existing SPA contract. Requires a valid **JWT Bearer token**.

| Method | Route | Description |
|---|---|---|
| `GET` | `/api/auth/me` | Get the current authenticated user's profile |
| `PATCH` | `/api/users/me` | Update the current authenticated user's profile |
| `POST` | `/api/users/me/avatar` | Upload the current user's profile avatar |
| `GET` | `/api/users/{user_id}/avatar` | Public proxy to fetch a user's avatar image |
| `POST` | `/api/users/me/details` | Create/overwrite the extended user profile (`user_details`) |
| `GET` | `/api/users/me/details` | Fetch the current user's extended profile |
| `PUT` | `/api/users/me/details` | Partially update the current user's extended profile |

The extended profile (`user_details`) is a 1:1 table on `user_id`, holding name, email, mobile, DOB, gender, nationality, communication preferences, avatar, and profile status (admin-managed).

### Token Analytics Endpoints

All require a valid **JWT Bearer token** (admin routes additionally require `role=admin`).

| Method | Route | Description |
|---|---|---|
| `GET` | `/api/v1/analytics/tokens/me` | Current user's token usage across all their workspaces |
| `GET` | `/api/v1/analytics/tokens/totals/me` | Current user's cumulative token/cost totals |
| `GET` | `/api/v1/analytics/tokens/workspaces/{workspace_id}` | Token usage summary for a single workspace |
| `GET` | `/api/v1/analytics/admin/tokens` | Platform-wide token analytics (admin) |

Token usage is recorded per LLM call via `TokenTrackingService` (which estimates USD cost from a model-pricing table) and aggregated per user, per workspace, per source, and per model.

### Contact Endpoint

| Method | Route | Auth | Description |
|---|---|---|---|
| `POST` | `/api/v1/contact` | ❌ | Public "Get in Touch" form submission — forwards to the support inbox via a background email job |

### Billing Endpoints

Razorpay subscription billing is mounted **unversioned** under `/api`.

| Method | Route | Auth | Description |
|---|---|---|---|
| `GET` | `/api/billing/plans` | ❌ | List active subscription plans |
| `POST` | `/api/billing/subscribe` | ✅ | Create a Razorpay subscription (returns a Checkout short URL) |
| `POST` | `/api/billing/verify` | ✅ | Verify a subscription payment signature (best-effort; webhook is the source of truth) |
| `GET` | `/api/billing/subscription` | ✅ | Get the current user's subscription status |
| `POST` | `/api/billing/cancel` | ✅ | Cancel a subscription at the end of the current cycle |
| `POST` | `/api/billing/webhook` | ❌ | Razorpay webhook (signature-verified) that confirms/advances subscription state and records payments |

> **Subscription enforcement:** access to subscription-gated features is controlled by the `SUBSCRIPTION_ENFORCED` flag. It defaults to `true` (secure by default) and is only bypassed when explicitly set to a falsey value (e.g. `SUBSCRIPTION_ENFORCED=false`) for local development. When enforced, only a webhook-confirmed `active` subscription grants paid entitlement (the `member` role).

---

### Orchestration Endpoints

| Method | Route | Auth | Description |
|---|---|---|---|
| `POST` | `/api/v1/orchestration/run` | ✅ | Run the full idea-validation agent pipeline for a workspace |

---

### Health & Root

| Method | Route | Description |
|---|---|---|
| `GET` | `/` | Root — welcome message and available routes |
| `GET` | `/health` | Server health, active LLM provider, and loaded skills |

---

## LLM Providers

The backend supports four LLM providers. Set `DEFAULT_PROVIDER` in your `.env` to switch:

| Provider | `DEFAULT_PROVIDER` value | Key Variable |
|---|---|---|
| HuggingFace Inference API | `huggingface` | `HF_TOKEN` |
| OpenAI | `openai` | `OPENAI_API_KEY` |
| Anthropic Claude | `anthropic` | `ANTHROPIC_API_KEY` |
| Azure OpenAI | `azure_openai` | `AZURE_OPENAI_MODEL` |

**Anthropic and Azure OpenAI are currently Phase 2+ stubs in the codebase.** Both provider classes exist, but their `complete()` implementations raise `NotImplementedError`. The usable providers today are **HuggingFace and OpenAI**.

---

## Skills System

Skills are Markdown instruction sets (with YAML frontmatter) loaded at startup into the skill registry. They provide the AI Mentor and agents with domain-specific capabilities.

| Skill File | Purpose |
|---|---|
| `ai_mentor_core_skill.md` | Core mentor conversation and idea extraction |
| `ai_idea_validation_mentor_skill.md` | Deep idea validation guidance |
| `idea_validation_skill.md` | Structured idea validation framework |
| `market_research_skill.md` | Market sizing and competitive analysis |
| `financial_readiness_skill.md` | Financial viability assessment |
| `gtm_strategy_skill.md` | Go-to-market strategy guidance |
| `survey_intelligence_skill.md` | Survey design and analysis |
| `survey_intelligence_agent_post_surveylink.md` | Post-survey-link response intelligence (SI.11–SI.44) |

Skills are loaded from `app/skills/` and registered automatically on startup.

---

## Testing & Quality

The repository includes an async test suite under `tests/` (30+ files, 400+ tests):

- `tests/test_auth.py` covers registration, OTP verification, login, forgot-password, password changes, admin login, current-user dependency behavior, and transactional-email job dispatch.
- `tests/test_email_templates.py` covers transactional email template rendering (registration welcome, password-reset confirmation).
- `tests/test_background_jobs.py` covers the async email job dispatcher — success, retry/backoff, permanent failure, unknown job types.
- `tests/test_admin.py` and `tests/test_admin_analytics.py` cover admin-only routes — user listing, cross-user surveys, user-growth stats, and responses.
- `tests/test_profile.py` covers the unversioned `/api/auth/me` and `/api/users/me` routes.
- `tests/test_workspaces.py` covers workspace CRUD and ownership enforcement.
- `tests/test_workspace_subresources.py` covers chat, state, reset, and report export/download.
- `tests/test_workspace_attachments.py` and `tests/test_workspace_attachment_service.py` cover attachment upload, listing, and deletion.
- `tests/test_attachment_processing.py` and `tests/test_attachment_processor_unit.py` cover PDF/DOCX/link/image content extraction.
- `tests/test_s3_storage_service.py` covers the local/S3 storage backend.
- `tests/test_surveys.py` covers survey CRUD, public submission, and export.
- `tests/test_survey_service_unit.py` covers the agent-result → survey auto-sync helper.
- `tests/test_questionnaire.py` covers questionnaire validation, admin question management, and answer submission flows.
- `tests/test_orchestration.py` covers the agentic orchestration endpoint.
- `tests/test_base_agent.py`, `test_idea_validation_agent.py`, `test_market_research_agent.py`, `test_survey_intelligence_agent.py` cover individual agent implementations.
- `tests/test_research_trace_service.py` covers the SSE research-query/source streaming service.
- `tests/test_web_search_service_unit.py` and `tests/test_web_scraper_service_unit.py` cover the web search/scraper services with mocked HTTP (no live network).
- `tests/test_web_tools.py` covers the live web-search/scraper paths and MCP web tools.
- `tests/test_user_details_service.py` covers the extended profile (`user_details`) service.
- `tests/test_billing.py` covers the Razorpay billing service + API (plans, subscribe, verify, cancel, webhook, entitlement).
- `tests/test_certificate.py` covers Certificate of Completion PDF generation.
- `tests/test_token_tracking.py` covers LLM token usage recording and analytics.
- `tests/test_contact.py` covers the public contact-form endpoint and job dispatch.
- `tests/test_database_constraints.py` covers schema constraints, foreign keys, indexes, and integrity failures.

Shared fixtures live in `tests/conftest.py` and provide:

- an async test database session
- an ASGI client wired to the FastAPI app
- automatic dependency overrides for database access and email job dispatch

### Running Tests

```bash
# Run the full suite
pytest

# Run a single file
pytest tests/test_auth.py

# Run a single test
pytest tests/test_auth.py::test_register_success_persists_user_and_dispatches_otp

# Run with a coverage report printed to the terminal (missing-line numbers included)
pytest --cov=app --cov-report=term-missing

# Also generate a browsable HTML coverage report (writes to htmlcov/index.html)
pytest --cov=app --cov-report=term-missing --cov-report=html
```

By default, the suite runs against an in-memory SQLite database (`sqlite+aiosqlite:///:memory:`) — no `DATABASE_URL`/PostgreSQL setup is required to run tests. Set `TEST_DATABASE_URL` to point at a real PostgreSQL instance instead if you want to test against the production database engine.

`--cov=app` reads scope/omit rules from `.coveragerc` at the repo root (excludes deprecated stub routers, `__init__.py` files, and a few other modules from the reported percentage — see that file for the full list).

Notes:

- The suite uses `pytest`, `pytest-asyncio`, and `pytest-cov`.
- `test_admin_script.py`, `test_llm_connectivity.py`, `test_llm_streaming.py`, `test_mcp_servers.py`, `test_full_pipeline_up_to_survey_agent.py`, `test_survey_agent_validation.py`, `test_analyze_endpoint.py`, `test_email.py`, and `test_market_research_agent_realtime.py` are manual/dev smoke-test scripts at the repo root — they are not part of the `pytest` suite under `tests/` and are run directly (e.g. `python test_admin_script.py`).
- The codebase follows the standard FastAPI split: routers stay thin, services hold business logic, and Pydantic models define request and response contracts.

---

## Rate Limiting

Rate limiting is implemented with SlowAPI and applies per client IP. Exceeding a limit returns `HTTP 429 Too Many Requests`.

Current route limits:

| Route group | Limit |
|---|---|
| `POST /api/v1/auth/refresh` | 30 requests/minute |
| `POST /api/v1/auth/logout` | 30 requests/minute |
| `POST /api/v1/auth/register` | 5 requests/minute |
| `POST /api/v1/auth/verifyOTP` | 5 requests/minute |
| `POST /api/v1/auth/resendOTP` | 3 requests/minute |
| `POST /api/v1/auth/login` | 5 requests/minute |
| `POST /api/v1/auth/verify-login` | 5 requests/minute |
| `POST /api/v1/auth/admin/login` | 5 requests/minute |
| `POST /api/v1/auth/forgot-password/request` | 5 requests/minute |
| `POST /api/v1/auth/forgot-password/verify` | 5 requests/minute |
| `POST /api/v1/auth/forgot-password/reset` | 3 requests/minute |
| `POST /api/v1/auth/change-password` | 5 requests/minute |
| Workspace create / update / delete | 20 requests/minute |
| Workspace list / get / state | 60 requests/minute |
| Workspace chat / report download / report export | 30 requests/minute |
| `POST /api/v1/contact` | 10 requests/minute |
| `GET /api/billing/plans` / `GET /api/billing/subscription` | 60 requests/minute |
| `POST /api/billing/subscribe` / `POST /api/billing/cancel` | 10 requests/minute |
| `POST /api/billing/verify` | 20 requests/minute |
| `POST /api/billing/webhook` | 240 requests/minute |

---

## Security

- **Passwords** are hashed using PBKDF2-HMAC-SHA256 (never stored in plain text)
- **OTP MFA** is required to complete both registration and login; **Google SSO** is supported as an alternative sign-in path
- **JWT tokens** use HS256 signing with configurable expiry
- **Ownership enforcement** — all workspace operations verify `workspace.user_id == current_user.id` (403 Forbidden on mismatch)
- **Subscription gate** — `SUBSCRIPTION_ENFORCED` (default `true`) blocks subscription-only features unless the user holds a webhook-confirmed `active` subscription; the flag only opens locally when explicitly set to `false`
- **Webhook signature verification** — Razorpay webhooks are verified against `RAZORPAY_WEBHOOK_SECRET` before processing
- **JWT secret** — the server refuses to start in production mode if `JWT_SECRET_KEY` is set to the insecure default value
- **CORS** — permissive (`*`) in DEBUG mode only; restricted to `ALLOWED_ORIGINS` in production

## Support ticket notifications

Support tickets are stored in `support_tickets`. Migration `0043` imports the
existing `data/tickets.json` once, preserving ticket IDs, messages, and private
notes. Back up that file before applying the migration; it remains unchanged.
The API now derives ticket ownership and administrator identity from the signed-in
user. Users can only access their own tickets, and internal notes are admin-only.

Admin assignments, unassignments, status changes (including Resolved and Closed),
and replies enqueue an email to the ticket owner. Internal notes and read markers
do not send email. Re-selecting the current status or assignee is a no-op.

`support_ticket_events` captures each substantive action, actor, timestamp,
old/new values or reply, recipient, and email state (`not_required`, `pending`,
`sent`, `failed`). Ticket changes and pending email records commit together.
The application polls the durable outbox every five seconds, using the existing
SMTP configuration. Failed sends retry after 30 and 60 seconds; after three
attempts the failure and its error remain available for investigation. Pending
jobs survive restarts. `sent` means accepted by SMTP, not confirmed inbox delivery.
A crash between SMTP acceptance and the database commit can result in a duplicate.

Example delivery-history query:

```sql
SELECT ticket_id, action, actor_id, created_at, recipient,
       email_status, attempts, sent_at, last_error
FROM support_ticket_events
ORDER BY created_at DESC;
```

After correcting an SMTP problem, an operator can explicitly retry a failed event:

```sql
UPDATE support_ticket_events
SET email_status = 'pending', attempts = 0, next_attempt_at = CURRENT_TIMESTAMP
WHERE id = '<event-id>' AND email_status = 'failed';
```
