"""
app/models/workspace_models.py
────────────────────────────────────────────────────────────────────────────────
Pydantic request/response models for all Workspace endpoints & sub-resources.

Endpoints covered:
  POST   /api/v1/workspaces                        → CreateWorkspaceRequest → WorkspaceResponse
  GET    /api/v1/workspaces                        → WorkspaceListResponse
  GET    /api/v1/workspaces/{id}                   → WorkspaceResponse
  DELETE /api/v1/workspaces/{id}                   → DeleteWorkspaceResponse (archives, is_delete=true)
  DELETE /api/v1/workspaces/{id}/permanent         → HardDeleteWorkspaceResponse (irreversible)
  PATCH  /api/v1/workspaces/{id}/restore           → RestoreWorkspaceResponse (is_delete=false)
  POST   /api/v1/workspaces/{id}/chat              → WorkspaceChatRequest   → WorkspaceChatResponse
  GET    /api/v1/workspaces/{id}/state             → WorkspaceStateResponse
  POST   /api/v1/workspaces/{id}/reset             → WorkspaceStateResponse
  GET    /api/v1/workspaces/{id}/reports/{agent}   → Stream File Download (PDF/Doc)
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Request Models ─────────────────────────────────────────────────────────────

class CreateWorkspaceRequest(BaseModel):
    """Payload for POST /api/v1/workspaces."""
    name: str = Field(..., min_length=1, max_length=100, description="Workspace name")
    description: Optional[str] = Field(None, description="Optional workspace description")


class UpdateWorkspaceRequest(BaseModel):
    """Payload for PUT /api/v1/workspaces/{workspace_id}."""
    name: str = Field(..., min_length=1, max_length=100, description="Updated workspace name")
    description: Optional[str] = Field(None, description="Updated workspace description (can be null/empty)")


class AttachmentInput(BaseModel):
    """Attachment item for mentor chat (image, pdf, doc, link)."""
    type: str = Field(..., description="Attachment type: image | pdf | doc | link")
    name: Optional[str] = Field(None, description="Filename or link title")
    url_or_data: str = Field(..., description="Base64 encoded file content (for uploads) or HTTP URL (for web links)")
    mime_type: Optional[str] = Field(None, description="MIME type e.g. application/pdf, image/png, text/plain")


class WorkspaceChatRequest(BaseModel):
    """Payload for POST /api/v1/workspaces/{id}/chat."""
    message: str = Field(..., min_length=1, description="Message to send to AI Mentor inside this workspace")
    attachments: Optional[List[AttachmentInput]] = Field(default_factory=list, description="Optional attachments (images, PDFs, docs, links)")



class ExportWorkspaceReportRequest(BaseModel):
    """Payload for POST /api/v1/workspaces/{id}/reports/export."""
    agent_name: str = Field("idea_validation_agent", description="idea_validation_agent | market_research_agent | full")
    format: str = Field("pdf", description="Export format. PDF is the only supported report output.")


class CertificateRequest(BaseModel):
    """Payload for POST /api/v1/workspaces/{id}/certificate."""
    name: Optional[str] = Field(
        None,
        min_length=1,
        max_length=100,
        description="Name to display on the certificate. Falls back to the user's display name/email prefix when omitted.",
    )


# ── Response Models ────────────────────────────────────────────────────────────

class WorkspaceResponse(BaseModel):
    """Returned for a single workspace."""
    id: int
    user_id: int
    name: str
    description: Optional[str] = None
    state: str = "GATHERING_INFO"
    is_delete: bool = False
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WorkspaceListResponse(BaseModel):
    """Returned for a list of workspaces."""
    total: int
    workspaces: List[WorkspaceResponse]


class WorkspaceStateResponse(BaseModel):
    """Returned for GET/POST /api/v1/workspaces/{id}/state or /reset."""
    id: int
    user_id: int
    name: str
    description: Optional[str] = None
    state: str = "GATHERING_INFO"
    idea: Dict[str, Any] = Field(default_factory=dict)
    conversation_history: List[Dict[str, Any]] = Field(default_factory=list)
    validation_result: Optional[Dict[str, Any]] = None
    is_delete: bool = False
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WorkspaceChatResponse(BaseModel):
    """Returned after sending a message to AI Mentor in a workspace."""
    reply: str
    workspace_id: int
    state: str
    idea: Dict[str, Any]
    validation_result: Optional[Dict[str, Any]] = None


class DeleteWorkspaceResponse(BaseModel):
    """Returned after a workspace is archived (soft-deleted)."""
    status: str = "success"
    message: str = "Workspace archived successfully."
    workspace_id: int


class WorkspaceSurveyQuestionItem(BaseModel):
    """Individual question item for editing workspace survey questions."""
    question_text: str = Field(..., min_length=1, description="Text of the survey question")
    question_type: str = Field(default="open_ended", description="Question input style: multiple_choice | open_ended | rating_scale | etc.")
    target_hypothesis: Optional[str] = Field(default=None, description="Assumption or pain point tested by this question")
    options: Optional[List[str]] = Field(default=None, description="Answer options for choice-based questions")


class UpdateWorkspaceSurveyQuestionsRequest(BaseModel):
    """Payload for PUT /api/v1/workspaces/{id}/survey/questions."""
    survey_title: Optional[str] = Field(None, description="Updated title for the survey")
    survey_objective: Optional[str] = Field(None, description="Updated objective for the survey")
    questions: List[WorkspaceSurveyQuestionItem] = Field(..., description="Full list of customized survey questions")


class UpdateWorkspaceSurveyQuestionsResponse(BaseModel):
    """Returned after a user updates their workspace survey questions."""
    status: str = "success"
    message: str = "Workspace survey questions updated successfully."
    workspace_id: int
    survey_title: Optional[str] = None
    survey_objective: Optional[str] = None
    questions: List[Dict[str, Any]]

    is_delete: bool = True


class HardDeleteWorkspaceResponse(BaseModel):
    """Returned after a workspace is permanently deleted."""
    status: str = "success"
    message: str = "Workspace permanently deleted."
    workspace_id: int


class RestoreWorkspaceResponse(BaseModel):
    """Returned after a workspace is restored from the archive."""
    status: str = "success"
    message: str = "Workspace restored successfully."
    workspace_id: int
    is_delete: bool = False


# ── Workspace Attachment Models ────────────────────────────────────────────────

class WorkspaceAttachmentResponse(BaseModel):
    """Returned for a single workspace attachment record."""
    id: int
    user_id: int
    workspace_id: int
    file_name: str
    file_type: str  # 'image' | 'pdf' | 'doc'
    mime_type: str
    s3_key: str
    file_url: str
    file_size_bytes: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WorkspaceAttachmentListResponse(BaseModel):
    """Returned for a list of workspace attachment records."""
    total: int
    attachments: List[WorkspaceAttachmentResponse]


class DeleteAttachmentResponse(BaseModel):
    """Returned after a workspace attachment is deleted."""
    status: str = "success"
    message: str = "Attachment deleted successfully."
    attachment_id: int
    workspace_id: int

