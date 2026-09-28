"""
Axiora Pulse – Core AI Orchestration Engine
FastAPI Application Entry Point
──────────────────────────────────────────────────────────────────────────────
Start the server:
  uvicorn main:app --reload --host 0.0.0.0 --port 8000

Swagger UI:   http://localhost:8000/docs
ReDoc:        http://localhost:8000/redoc
Health:       http://localhost:8000/health
"""
import logging
import asyncio
from contextlib import suppress
import os
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

# Load .env before anything else
load_dotenv()

# ── App configuration from environment ─────────────────────────────────────────
APP_NAME        = os.getenv("APP_NAME", "Axiora Pulse AI Engine")
APP_VERSION     = os.getenv("APP_VERSION", "1.0.0")
DEBUG           = os.getenv("DEBUG", "true").lower() in ("true", "1", "t", "yes", "y")
DEFAULT_PROVIDER = os.getenv("DEFAULT_PROVIDER", "huggingface")
DEFAULT_MODEL   = os.getenv("DEFAULT_MODEL", "meta-llama/Llama-3.1-8B-Instruct")

# Per-provider model selection (takes priority over DEFAULT_MODEL for each provider)
HF_MODEL        = os.getenv("HF_MODEL", DEFAULT_MODEL)
OPENAI_MODEL    = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
AZURE_OPENAI_MODEL = os.getenv("AZURE_OPENAI_MODEL", "gpt-4o")

# Resolve the model that will actually be used for the active provider
_PROVIDER_MODEL_MAP = {
    "huggingface":  HF_MODEL,
    "openai":       OPENAI_MODEL,
    "anthropic":    ANTHROPIC_MODEL,
    "azure_openai": AZURE_OPENAI_MODEL,
}
ACTIVE_MODEL = _PROVIDER_MODEL_MAP.get(DEFAULT_PROVIDER, DEFAULT_MODEL)

HF_TOKEN        = os.getenv("HF_TOKEN")
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
JWT_SECRET_KEY  = os.getenv("JWT_SECRET_KEY", "axiora-pulse-change-this-secret-in-production")
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "")

from app.core.logging import setup_logging

# Logging configured before any other imports
setup_logging()
logger = logging.getLogger(__name__)

from app.core.limiter import limiter
from app.db.database import run_migrations, AsyncSessionLocal
from app.services.auth_service import seed_admin_user
from app.skills.skill_registry import skill_registry


# ── Startup / Shutdown lifecycle ───────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── STARTUP ────────────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info(f"  {APP_NAME}  v{APP_VERSION}")
    logger.info("=" * 60)

    # Validate security config (JWT Secret validation)
    _validate_security_config()

    # Apply any pending DB migrations (Alembic upgrade head)
    database_ready = False
    try:
        await run_migrations()
        # Seed default admin user account
        async with AsyncSessionLocal() as session:
            await seed_admin_user(session)
        database_ready = True
    except Exception as exc:
        logger.error("⚠ Database initialization/migration failed: %s", exc)
        logger.warning("⚠ Server starting in degraded mode. Docs and non-DB endpoints remain accessible.")

    # Load all skill YAML files into the registry
    skill_registry.load_all()
    loaded = skill_registry.list_skills()
    logger.info(f"Skill registry ready: {loaded}")

    # Validate that the configured LLM provider credentials exist
    _validate_provider_config()

    # Surface the subscription/payment gate state loudly — a shared environment
    # (QA/prod) running with enforcement OFF would let everyone bypass payment.
    from app.services.billing_service import SUBSCRIPTION_ENFORCED
    if SUBSCRIPTION_ENFORCED:
        logger.info("Subscription enforcement: ENABLED")
    else:
        logger.warning(
            "⚠  SUBSCRIPTION ENFORCEMENT DISABLED — all users are treated as paid. "
            "For LOCAL DEV only; never run QA/production with SUBSCRIPTION_ENFORCED=false."
        )

    logger.info("Server is ready.")
    if DEBUG:
        logger.info(f"  Docs  →  http://localhost:8000/docs")
    logger.info(f"  Health →  http://localhost:8000/health")
    logger.info("=" * 60)

    from app.workers.ticket_notifications import run_ticket_notifications
    notification_worker = asyncio.create_task(run_ticket_notifications()) if database_ready else None
    try:
        yield
    finally:
        if notification_worker is not None:
            notification_worker.cancel()
            with suppress(asyncio.CancelledError):
                await notification_worker

    # ── SHUTDOWN ────────────────────────────────────────────────────────────────
    logger.info(f"Shutting down {APP_NAME}…")


def _validate_provider_config() -> None:
    """Warn at startup if the configured provider has no API key."""
    provider = DEFAULT_PROVIDER
    if provider == "huggingface" and not HF_TOKEN:
        logger.warning(
            "⚠  HF_TOKEN is not set. "
            "Add it to your .env file. "
            "Get a token at https://huggingface.co/settings/tokens"
        )
    elif provider == "openai" and not OPENAI_API_KEY:
        logger.warning("⚠  OPENAI_API_KEY is not set. Add it to your .env file.")
    else:
        logger.info(f"LLM provider: {provider} | model: {ACTIVE_MODEL}")


def _validate_security_config() -> None:
    """Validate JWT secret is customized if not in debug mode."""
    if JWT_SECRET_KEY == "axiora-pulse-change-this-secret-in-production" and not DEBUG:
        logger.critical("CRITICAL SECURITY ERROR: JWT_SECRET_KEY is using the insecure default value in production mode!")
        raise ValueError(
            "CRITICAL SECURITY ERROR: JWT_SECRET_KEY is using the insecure default value in production mode! "
            "Please configure JWT_SECRET_KEY in your .env file."
        )


# ── FastAPI app ────────────────────────────────────────────────────────────────

# Tags listed in alphabetical order — FastAPI preserves this order in Swagger UI.
_OPENAPI_TAGS = [
    {
        "name": "Admin",
        "description": "Administrator-only dashboard data.",
    },
    {
        "name": "Auth",
        "description": "User registration and login. Issues JWT Bearer tokens for authenticated access.",
    },
    {
        "name": "Health",
        "description": "Global server health check — confirms startup, LLM provider, and loaded skills.",
    },
    {
        "name": "Orchestration",
        "description": "Idea-validation orchestration workflows powered by MCP, Skills, and Agentic Workflows.",
    },
    {
        "name": "Root",
        "description": "Root discovery endpoint — lists available API routes.",
    },
    {
        "name": "Workspaces",
        "description": (
            "Workspace management and all AI Mentor sub-resources. "
            "Create, list, retrieve, delete workspaces, chat with AI Mentor, "
            "retrieve validation state, reset conversations, and download agent reports."
        ),
    },
]

app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description=(
        "Core AI Orchestration Engine for Axiora Pulse. "
        "Converts founder ideas into structured validation journeys using "
        "MCP, Skills, and Agentic Workflows."
    ),
    openapi_tags=_OPENAPI_TAGS,
    docs_url="/docs" if DEBUG else None,
    redoc_url="/redoc" if DEBUG else None,
    lifespan=lifespan,
)

# ── Rate Limiter ──────────────────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS ──────────────────────────────────────────────────────────────────────────
origins = [o.strip() for o in ALLOWED_ORIGINS.split(",") if o.strip()]
if DEBUG and (not origins or "*" in origins):
    origins = ["*"]
    allow_credentials = False
else:
    allow_credentials = "*" not in origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request logging ───────────────────────────────────────────────────────────
# Logs one access line per request: method, path, status code, duration.
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (time.monotonic() - start) * 1000
        logger.exception(
            f"{request.method} {request.url.path} -> 500 ({duration_ms:.1f}ms)"
        )
        raise

    duration_ms = (time.monotonic() - start) * 1000
    logger.info(
        f"{request.method} {request.url.path} -> {response.status_code} ({duration_ms:.1f}ms)"
    )
    return response


# ── Routers ────────────────────────────────────────────────────────────────────
from app.api.v1 import auth as auth_router
from app.api.v1 import admin as admin_router
from app.api.v1 import interactive_questionnaire as interactive_questionnaire_router
from app.api.v1 import questionnaire as questionnaire_router
from app.api.v1 import feedback_questionnaire as feedback_questionnaire_router
from app.api.v1 import user_feedback as user_feedback_router
from app.api.v1 import plans as plans_router
from app.api.v1 import orchestration as orchestration_router
from app.api.v1 import workspace as workspace_router
from app.api.v1 import surveys as surveys_router
from app.api.v1 import analytics as analytics_router
from app.api.v1 import contact as contact_router
from app.api.v1 import tickets as tickets_router
from app.api import profile as profile_router
from app.api import billing as billing_router

app.include_router(auth_router.router, prefix="/api/v1")
app.include_router(admin_router.router, prefix="/api/v1")
app.include_router(interactive_questionnaire_router.router, prefix="/api/v1")
app.include_router(questionnaire_router.router, prefix="/api/v1")
app.include_router(feedback_questionnaire_router.admin_router, prefix="/api/v1")
app.include_router(feedback_questionnaire_router.user_router, prefix="/api/v1")
app.include_router(user_feedback_router.router, prefix="/api/v1")
app.include_router(plans_router.router, prefix="/api/v1")
app.include_router(orchestration_router.router, prefix="/api/v1")
app.include_router(workspace_router.router, prefix="/api/v1/workspaces")
app.include_router(workspace_router.router, prefix="/api/v1/workspace")
app.include_router(surveys_router.router, prefix="/api/v1")
app.include_router(analytics_router.router, prefix="/api/v1")
app.include_router(tickets_router.router, prefix="/api/v1")
# Public (unauthenticated) landing-page contact form.
app.include_router(contact_router.router, prefix="/api/v1")
# These paths intentionally remain unversioned to match the existing SPA contract.
app.include_router(profile_router.auth_router, prefix="/api")
app.include_router(profile_router.users_router, prefix="/api")
app.include_router(billing_router.router, prefix="/api")

# Serve local file uploads (avatars, attachments) statically for browser access
from fastapi.staticfiles import StaticFiles
os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")



# ── Root endpoints ─────────────────────────────────────────────────────────────

@app.get("/", tags=["Root"], summary="Root endpoint")
async def root() -> dict:
    return {
        "message": f"Welcome to {APP_NAME}",
        "version": APP_VERSION,
        "docs": "/docs",
        "health": "/health",
        "endpoints": {
            "workspaces":         "POST   /api/v1/workspaces",
            "workspace_chat":     "POST   /api/v1/workspaces/{id}/chat",
            "workspace_state":    "GET    /api/v1/workspaces/{id}/state",
            "workspace_reset":    "POST   /api/v1/workspaces/{id}/reset",
            "workspace_report":   "GET    /api/v1/workspaces/{id}/reports/{agent_name}",
            "workspace_export":   "POST   /api/v1/workspaces/{id}/reports/export",
            "validate_idea":      "POST   /api/v1/orchestration/run",
        },
    }


@app.get("/health", tags=["Health"], summary="Global health check")
async def health() -> dict:
    """
    Returns overall server health, loaded skills, and provider configuration.
    Use this to confirm the server started correctly.
    """
    skills = skill_registry.list_skills()

    return {
        "status": "healthy",
        "app": APP_NAME,
        "version": APP_VERSION,
        "llm_provider": DEFAULT_PROVIDER,
        "llm_model": ACTIVE_MODEL,
        "skills_loaded": skills,
        "skills_count": len(skills),
        "provider_configured": _is_provider_configured(),
    }


def _is_provider_configured() -> bool:
    provider = DEFAULT_PROVIDER
    if provider == "huggingface":
        return bool(HF_TOKEN)
    elif provider == "openai":
        return bool(OPENAI_API_KEY)
    elif provider == "anthropic":
        return bool(ANTHROPIC_API_KEY)
    return False
