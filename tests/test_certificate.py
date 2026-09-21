import pytest
import fitz
from fastapi import status
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from types import SimpleNamespace

from app.core.dependencies import get_current_user
from app.core.security import hash_password_async
from app.db.models import Role, User, Workspace
from app.services.certificate_service import CertificateService


# ── Helpers ──────────────────────────────────────────────────────────────────

async def create_test_user(
    db_session: AsyncSession,
    *,
    username: str,
    display_name: str | None = None,
    role: str = "member",
) -> User:
    role_obj = (await db_session.execute(select(Role).where(Role.name == role))).scalar_one_or_none()
    if role_obj is None:
        role_obj = Role(name=role, description=f"{role} role")
        db_session.add(role_obj)
        await db_session.flush()

    user = User(
        username=username,
        password=await hash_password_async("Test@12345"),
        display_name=display_name,
        register_mfa=True,
        role=role_obj,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def create_workspace(
    db_session: AsyncSession,
    *,
    user_id: int,
    name: str,
    state: str = "GATHERING_INFO",
    validation_result: dict | None = None,
) -> Workspace:
    workspace = Workspace(
        user_id=user_id,
        name=name,
        state=state,
        idea={"idea_title": name},
        conversation_history=[],
        validation_result=validation_result,
    )
    db_session.add(workspace)
    await db_session.commit()
    await db_session.refresh(workspace)
    return workspace


def authenticate_as(user: User) -> None:
    role_name = user._primary_role

    def _has_role(name: str) -> bool:
        return role_name == name

    current_user = SimpleNamespace(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=role_name,
        has_role=_has_role,
    )

    async def _mock_current_user():
        return current_user

    from main import app

    app.dependency_overrides[get_current_user] = _mock_current_user


SAMPLE_VALIDATION_RESULT = {
    "validation_score": 82.5,
    "confidence_rating": 0.85,
    "verdict": "BUILD",
    "strengths": ["Strong market demand"],
    "risks": ["Competition"],
    "assumptions": [],
    "recommendations": [],
    "agent_results": {},
}


# ── Unit tests: CertificateService ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_certificate_returns_valid_pdf():
    cert_service = CertificateService()
    pdf_bytes = cert_service.generate_certificate("John Doe")
    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 0
    assert pdf_bytes[:5] == b"%PDF-"

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        assert doc[0].rect.width > doc[0].rect.height
    finally:
        doc.close()


@pytest.mark.asyncio
async def test_generate_certificate_with_simple_name():
    cert_service = CertificateService()
    pdf_bytes = cert_service.generate_certificate("Alice")
    assert pdf_bytes[:5] == b"%PDF-"


@pytest.mark.asyncio
async def test_generate_certificate_embeds_certificate_metadata():
    cert_service = CertificateService()
    pdf_bytes = cert_service.generate_certificate(
        "Alice",
        certificate_id="Pulse/2026/09/0001",
        issue_date="01 September 2026",
    )

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        text = doc[0].get_text()
    finally:
        doc.close()

    assert "Alice" in text
    assert "Pulse/2026/09/0001" in text
    assert "01 September 2026" in text


# ── API tests: POST /{workspace_id}/certificate ─────────────────────────────

@pytest.mark.asyncio
async def test_download_certificate_returns_pdf(client: AsyncClient, db_session: AsyncSession):
    user = await create_test_user(db_session, username="john@example.com")
    workspace = await create_workspace(
        db_session,
        user_id=user.id,
        name="My Startup",
        state="VALIDATED",
        validation_result=SAMPLE_VALIDATION_RESULT,
    )
    authenticate_as(user)

    resp = await client.post(
        f"/api/v1/workspaces/{workspace.id}/certificate",
        json={"name": "John Doe"},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:5] == b"%PDF-"
    assert "attachment" in resp.headers.get("content-disposition", "")


@pytest.mark.asyncio
async def test_download_certificate_uses_email_prefix_when_no_display_name(
    client: AsyncClient, db_session: AsyncSession,
):
    user = await create_test_user(db_session, username="john.doe@gmail.com")
    workspace = await create_workspace(
        db_session,
        user_id=user.id,
        name="Test Idea",
        state="VALIDATED",
        validation_result=SAMPLE_VALIDATION_RESULT,
    )
    authenticate_as(user)

    resp = await client.post(f"/api/v1/workspaces/{workspace.id}/certificate", json={})
    assert resp.status_code == status.HTTP_200_OK
    assert resp.content[:5] == b"%PDF-"


@pytest.mark.asyncio
async def test_download_certificate_uses_display_name_when_set(
    client: AsyncClient, db_session: AsyncSession,
):
    user = await create_test_user(db_session, username="john@example.com", display_name="Johnny")
    workspace = await create_workspace(
        db_session,
        user_id=user.id,
        name="Test Idea",
        state="VALIDATED",
        validation_result=SAMPLE_VALIDATION_RESULT,
    )
    authenticate_as(user)

    resp = await client.post(f"/api/v1/workspaces/{workspace.id}/certificate", json={})
    assert resp.status_code == status.HTTP_200_OK
    assert resp.content[:5] == b"%PDF-"


@pytest.mark.asyncio
async def test_download_certificate_uses_custom_name_from_payload(
    client: AsyncClient, db_session: AsyncSession,
):
    user = await create_test_user(db_session, username="john@example.com", display_name="Johnny")
    workspace = await create_workspace(
        db_session,
        user_id=user.id,
        name="Test Idea",
        state="VALIDATED",
        validation_result=SAMPLE_VALIDATION_RESULT,
    )
    authenticate_as(user)

    resp = await client.post(
        f"/api/v1/workspaces/{workspace.id}/certificate",
        json={"name": "      custom   name   "},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.content[:5] == b"%PDF-"


@pytest.mark.asyncio
async def test_download_certificate_rejects_unvalidated_workspace(
    client: AsyncClient, db_session: AsyncSession,
):
    user = await create_test_user(db_session, username="john@example.com")
    workspace = await create_workspace(
        db_session,
        user_id=user.id,
        name="Unvalidated Idea",
        state="GATHERING_INFO",
        validation_result=None,
    )
    authenticate_as(user)

    resp = await client.post(f"/api/v1/workspaces/{workspace.id}/certificate", json={"name": "John"})
    assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_download_certificate_requires_auth(client: AsyncClient, db_session: AsyncSession):
    from main import app
    app.dependency_overrides.pop(get_current_user, None)

    resp = await client.post("/api/v1/workspaces/1/certificate", json={"name": "John"})
    assert resp.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)
