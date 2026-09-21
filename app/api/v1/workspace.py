"""
app/api/v1/workspace.py
────────────────────────────────────────────────────────────────────────────────
Workspace router — CRUD and workspace-owned sub-resources:
  - Create, List, Retrieve, Delete workspaces
  - Send message to AI Mentor in workspace
  - Get full workspace dialogue state & validation results
  - Reset workspace conversation state
  - Export template-based PDF reports per agent for workspace

All endpoints require a valid JWT Bearer token (get_current_user dependency).

Routes:
  POST   /api/v1/workspaces                        → create_workspace
  GET    /api/v1/workspaces                        → list_workspaces
  GET    /api/v1/workspaces/user/{user_id}         → get_workspaces_by_user_id
  GET    /api/v1/workspaces/{id}                   → get_workspace
  PUT    /api/v1/workspaces/{id}                   → update_workspace
  DELETE /api/v1/workspaces/{id}                   → delete_workspace (archives, is_delete=true)
  DELETE /api/v1/workspaces/{id}/permanent         → hard_delete_workspace (irreversible)
  PATCH  /api/v1/workspaces/{id}/restore           → restore_workspace (is_delete=false)
  POST   /api/v1/workspaces/{id}/chat              → chat_workspace_mentor
  GET    /api/v1/workspaces/{id}/state             → get_workspace_state
  POST   /api/v1/workspaces/{id}/reset             → reset_workspace_mentor
  GET    /api/v1/workspaces/{id}/reports/{agent}   → download_workspace_agent_report
  POST   /api/v1/workspaces/{id}/reports/export    → export_workspace_report
  POST   /api/v1/workspaces/{id}/certificate       → download_certificate
"""
from fastapi import APIRouter, Depends, Query, Request, Response, UploadFile, File, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, require_export_enabled
from app.core.limiter import limiter
from app.db.database import get_db
from app.db.models import User
from app.models.workspace_models import (
    CreateWorkspaceRequest,
    CertificateRequest,
    DeleteAttachmentResponse,
    DeleteWorkspaceResponse,
    ExportWorkspaceReportRequest,
    HardDeleteWorkspaceResponse,
    RestoreWorkspaceResponse,
    UpdateWorkspaceRequest,
    UpdateWorkspaceSurveyQuestionsRequest,
    UpdateWorkspaceSurveyQuestionsResponse,
    WorkspaceAttachmentListResponse,
    WorkspaceAttachmentResponse,
    WorkspaceChatRequest,
    WorkspaceChatResponse,
    WorkspaceListResponse,
    WorkspaceResponse,
    WorkspaceStateResponse,
)
from app.models.survey_models import SurveyResponse
from app.services.survey_service import survey_service
from app.services.workspace_service import workspace_service
from app.services.workspace_attachment_service import workspace_attachment_service

router = APIRouter(tags=["Workspaces"])


# ── Create Workspace ──────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new workspace",
    description="Creates a new workspace scoped to the authenticated user.",
)
@limiter.limit("20/minute")
async def create_workspace(
    request: Request,
    payload: CreateWorkspaceRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceResponse:
    return await workspace_service.create_workspace(payload, current_user, db)


# ── List Workspaces ───────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=WorkspaceListResponse,
    status_code=status.HTTP_200_OK,
    summary="List all workspaces for the current user",
    description="Returns all workspaces owned by the authenticated user, filtered by is_delete (default false = active workspaces).",
)
@limiter.limit("60/minute")
async def list_workspaces(
    request: Request,
    is_delete: bool = Query(False, description="Filter by archive status: false = active, true = archived"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceListResponse:
    return await workspace_service.list_workspaces(current_user, db, is_delete)


# ── Get Workspaces by User ID ─────────────────────────────────────────

@router.get(
    "/user/{user_id}",
    response_model=WorkspaceListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get all workspaces for a specific user ID",
    description="Returns all workspaces belonging to the given user_id, filtered by is_delete (default false = active workspaces).",
)
@limiter.limit("60/minute")
async def get_workspaces_by_user_id(
    request: Request,
    user_id: int,
    is_delete: bool = Query(False, description="Filter by archive status: false = active, true = archived"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceListResponse:
    return await workspace_service.get_workspaces_by_user_id(user_id, current_user, db, is_delete)


# ── Get Workspace by ID ───────────────────────────────────────────────────────

@router.get(
    "/{workspace_id}",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a workspace by ID",
    description="Fetches a single workspace by its ID.",
)
@limiter.limit("60/minute")
async def get_workspace(
    request: Request,
    workspace_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceResponse:
    return await workspace_service.get_workspace(workspace_id, current_user, db)


# Update Workspace 

@router.put(
    "/{workspace_id}",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a workspace by ID",
    description="Updates the name and/or description of a workspace owned by the authenticated user.",
)
@limiter.limit("20/minute")
async def update_workspace(
    request: Request,
    workspace_id: int,
    payload: UpdateWorkspaceRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceResponse:
    return await workspace_service.update_workspace(workspace_id, payload, current_user, db)


# Delete (Archive) Workspace 

@router.delete(
    "/{workspace_id}",
    response_model=DeleteWorkspaceResponse,
    status_code=status.HTTP_200_OK,
    summary="Archive a workspace by ID",
    description="Soft-deletes a workspace by setting is_delete=true. Use the restore endpoint to undo.",
)
@limiter.limit("20/minute")
async def delete_workspace(
    request: Request,
    workspace_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DeleteWorkspaceResponse:
    return await workspace_service.delete_workspace(workspace_id, current_user, db)


# Permanently Delete Workspace

@router.delete(
    "/{workspace_id}/permanent",
    response_model=HardDeleteWorkspaceResponse,
    status_code=status.HTTP_200_OK,
    summary="Permanently delete a workspace by ID",
    description=(
        "Irreversibly deletes a workspace and all associated data (attachments, surveys). "
        "This cannot be undone. Use DELETE /{workspace_id} to archive instead if you may want to restore it later."
    ),
)
@limiter.limit("10/minute")
async def hard_delete_workspace(
    request: Request,
    workspace_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HardDeleteWorkspaceResponse:
    return await workspace_service.hard_delete_workspace(workspace_id, current_user, db)


# Restore Workspace

@router.patch(
    "/{workspace_id}/restore",
    response_model=RestoreWorkspaceResponse,
    status_code=status.HTTP_200_OK,
    summary="Restore an archived workspace by ID",
    description="Restores a previously archived workspace by setting is_delete=false.",
)
@limiter.limit("20/minute")
async def restore_workspace(
    request: Request,
    workspace_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RestoreWorkspaceResponse:
    return await workspace_service.restore_workspace(workspace_id, current_user, db)


# Workspace AI Mentor Chat Sub-resource 

@router.post(
    "/{workspace_id}/chat",
    response_model=WorkspaceChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Send a message to AI Mentor inside a workspace",
    description="Sends a message to the AI Mentor for the given workspace, updates idea context, and runs validation when ready.",
)
@limiter.limit("30/minute")
async def chat_workspace_mentor(
    request: Request,
    workspace_id: int,
    payload: WorkspaceChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceChatResponse:
    return await workspace_service.process_mentor_chat(workspace_id, payload, current_user, db)


# ── Workspace Full State Sub-resource ──────────────────────────────────────────

@router.get(
    "/{workspace_id}/state",
    response_model=WorkspaceStateResponse,
    status_code=status.HTTP_200_OK,
    summary="Get workspace dialogue history & validation state",
    description="Retrieves full conversation history, extracted idea details, and agent validation results for a workspace.",
)
@limiter.limit("60/minute")
async def get_workspace_state(
    request: Request,
    workspace_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceStateResponse:
    return await workspace_service.get_workspace_state(workspace_id, current_user, db)


# ── Workspace Reset Sub-resource ───────────────────────────────────────────────

@router.post(
    "/{workspace_id}/reset",
    response_model=WorkspaceStateResponse,
    status_code=status.HTTP_200_OK,
    summary="Reset workspace mentor conversation",
    description="Resets the conversation dialogue state and extracted idea context for a workspace.",
)
@limiter.limit("20/minute")
async def reset_workspace_mentor(
    request: Request,
    workspace_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceStateResponse:
    return await workspace_service.reset_workspace_mentor(workspace_id, current_user, db)


# ── Workspace Agent Report Download Sub-resource ───────────────────────────────

@router.get(
    "/{workspace_id}/reports/{agent_name}",
    summary="Download agent report for a workspace",
    description="Generates and downloads a template-based PDF report for a specific agent (idea_validation_agent, market_research_agent, or full) from a workspace.",
)
@limiter.limit("30/minute")
async def download_workspace_agent_report(
    request: Request,
    workspace_id: int,
    agent_name: str,
    format: str = Query("pdf", description="Export format. PDF is the only supported report output."),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await workspace_service.export_workspace_report(workspace_id, agent_name, format, current_user, db)


@router.post(
    "/{workspace_id}/reports/export",
    summary="Export agent report for a workspace via POST",
    description="Generates and downloads a template-based PDF agent report for the workspace.",
)
@limiter.limit("30/minute")
async def export_workspace_report(
    request: Request,
    workspace_id: int,
    payload: ExportWorkspaceReportRequest,
    current_user: User = Depends(require_export_enabled),
    db: AsyncSession = Depends(get_db),
):
    return await workspace_service.export_workspace_report(
        workspace_id=workspace_id,
        agent_name=payload.agent_name,
        export_format=payload.format,
        current_user=current_user,
        db=db
    )


# ── Certificate of Completion ────────────────────────────────────────────────

@router.post(
    "/{workspace_id}/certificate",
    summary="Download Certificate of Completion for a validated workspace",
    description="Generates and downloads a personalised Certificate of Completion PDF. The workspace must be in VALIDATED state. The name shown on the certificate can be supplied via the request body.",
)
@limiter.limit("10/minute")
async def download_certificate(
    request: Request,
    workspace_id: int,
    payload: CertificateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await workspace_service.generate_certificate(
        workspace_id,
        current_user,
        db,
        display_name=payload.name,
    )


# ── Get Workspace Survey (Sub-resource Alias) ──────────────────────────────────

@router.get(
    "/{workspace_id}/surveys",
    response_model=SurveyResponse,
    status_code=status.HTTP_200_OK,
    summary="Get survey by workspace ID",
    description="Fetches the survey questions associated with a specific workspace.",
)
@router.get(
    "/{workspace_id}/survey",
    response_model=SurveyResponse,
    status_code=status.HTTP_200_OK,
    summary="Get survey by workspace ID",
    description="Fetches the survey questions associated with a specific workspace.",
)
@limiter.limit("60/minute")
async def get_workspace_survey(
    request: Request,
    workspace_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SurveyResponse:
    return await survey_service.get_survey_by_workspace_id(workspace_id, current_user, db)


# ── Update Workspace Survey Questions (User Session) ─────────────────────────

@router.put(
    "/{workspace_id}/survey/questions",
    response_model=UpdateWorkspaceSurveyQuestionsResponse,
    status_code=status.HTTP_200_OK,
    summary="Update survey questions for a workspace during active user session",
    description="Allows regular logged-in users to edit, reorder, or customize the survey questions created for their workspace.",
)
@limiter.limit("30/minute")
async def update_workspace_survey_questions(
    request: Request,
    workspace_id: int,
    payload: UpdateWorkspaceSurveyQuestionsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UpdateWorkspaceSurveyQuestionsResponse:
    return await workspace_service.update_workspace_survey_questions(
        workspace_id=workspace_id,
        payload=payload,
        current_user=current_user,
        db=db
    )


# ── Workspace File Attachments Sub-resource ────────────────────────────────────

@router.post(
    "/{workspace_id}/attachments",
    response_model=WorkspaceAttachmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a file to a workspace",
    description=(
        "Uploads a file (image, PDF, or document) to the workspace's dedicated S3 path. "
        "Accepted MIME types: image/*, application/pdf, application/vnd.openxmlformats-officedocument.wordprocessingml.document, "
        "text/plain, text/markdown, text/csv."
    ),
)
@limiter.limit("30/minute")
async def upload_workspace_attachment(
    request: Request,
    workspace_id: int,
    file: UploadFile = File(..., description="File to upload (image, PDF, or doc)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceAttachmentResponse:
    return await workspace_attachment_service.upload_file(
        workspace_id=workspace_id,
        current_user=current_user,
        file=file,
        db=db,
    )


@router.get(
    "/{workspace_id}/attachments",
    response_model=WorkspaceAttachmentListResponse,
    status_code=status.HTTP_200_OK,
    summary="List all uploaded files for a workspace",
    description="Returns all uploaded files for the workspace, optionally filtered by type (image | pdf | doc).",
)
@limiter.limit("60/minute")
async def list_workspace_attachments(
    request: Request,
    workspace_id: int,
    file_type: str = Query(None, description="Filter by file type: image | pdf | doc"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceAttachmentListResponse:
    return await workspace_attachment_service.list_attachments(
        workspace_id=workspace_id,
        current_user=current_user,
        db=db,
        file_type=file_type,
    )


@router.get(
    "/{workspace_id}/attachments/{attachment_id}",
    response_model=WorkspaceAttachmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a single workspace attachment record",
    description="Fetches the metadata for a single uploaded file by its ID.",
)
@limiter.limit("60/minute")
async def get_workspace_attachment(
    request: Request,
    workspace_id: int,
    attachment_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceAttachmentResponse:
    return await workspace_attachment_service.get_attachment(
        workspace_id=workspace_id,
        attachment_id=attachment_id,
        current_user=current_user,
        db=db,
    )


@router.delete(
    "/{workspace_id}/attachments/{attachment_id}",
    response_model=DeleteAttachmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete a workspace attachment",
    description="Permanently deletes a file from S3 and removes its database record.",
)
@limiter.limit("30/minute")
async def delete_workspace_attachment(
    request: Request,
    workspace_id: int,
    attachment_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DeleteAttachmentResponse:
    return await workspace_attachment_service.delete_attachment(
        workspace_id=workspace_id,
        attachment_id=attachment_id,
        current_user=current_user,
        db=db,
    )
