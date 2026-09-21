"""
app/services/workspace_service.py
────────────────────────────────────────────────────────────────────────────────
Workspace service — all database operations & sub-resource handlers for workspaces.

Operations:
  create_workspace()             → Persist a new workspace scoped to the current user.
  list_workspaces()              → Fetch all workspaces belonging to the current user.
  get_workspace()                → Fetch a single workspace by ID (owner-enforced).
  get_workspaces_by_user_id()    → Fetch all workspaces for a given user_id (self-service enforced).
  delete_workspace()             → Archive (soft-delete) a workspace by ID (owner-enforced).
  hard_delete_workspace()        → Permanently delete a workspace and its data (owner-enforced).
  restore_workspace()            → Restore an archived workspace by ID (owner-enforced).
  process_mentor_chat()          → Process AI Mentor message in a workspace (owner-enforced).
  get_workspace_state()          → Fetch full workspace dialogue & validation state (owner-enforced).
  reset_workspace_mentor()       → Reset mentor dialogue for a workspace (owner-enforced).
  export_workspace_report()      → Export template-based PDF report for a workspace (owner-enforced).
"""
import logging
import re
from datetime import datetime, timezone
from typing import Optional, Tuple

from fastapi import HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, Workspace, WorkspaceAttachment
from app.models.workspace_models import (
    CreateWorkspaceRequest,
    DeleteWorkspaceResponse,
    HardDeleteWorkspaceResponse,
    RestoreWorkspaceResponse,
    UpdateWorkspaceRequest,
    UpdateWorkspaceSurveyQuestionsRequest,
    UpdateWorkspaceSurveyQuestionsResponse,
    WorkspaceChatRequest,
    WorkspaceChatResponse,
    WorkspaceListResponse,
    WorkspaceResponse,
    WorkspaceStateResponse,
)
from app.services.mentor_service import mentor_service, WorkspaceMentorState
from app.services.certificate_service import certificate_service
from app.services.report_service import report_service
from app.services.s3_storage_service import s3_storage_service
from app.services.survey_service import survey_service

logger = logging.getLogger(__name__)


class WorkspaceService:
    """Stateless service — all state lives in the DB session."""

    # ── Create ────────────────────────────────────────────────────────────────

    async def create_workspace(
        self,
        payload: CreateWorkspaceRequest,
        current_user: User,
        db: AsyncSession,
    ) -> WorkspaceResponse:
        """Create a new workspace owned by current_user."""
        now = datetime.now(timezone.utc)

        # Enforce the per-user workspace allowance (accumulating model). Counts BOTH
        # active and archived workspaces, since permanent delete is disabled so nothing
        # frees a slot. None = unlimited (admin / enforcement off).
        from app.services.entitlements_service import entitlements_service  # lazy: avoid import cycle

        allowed_workspaces, _ = await entitlements_service.get_caps(current_user, db)
        if allowed_workspaces is not None:
            total = (
                await db.execute(
                    select(func.count(Workspace.id)).where(
                        Workspace.user_id == current_user.id
                    )
                )
            ).scalar_one()
            if total >= allowed_workspaces:
                raise HTTPException(
                    status.HTTP_402_PAYMENT_REQUIRED,
                    f"You've reached your workspace limit of {allowed_workspaces}. "
                    "Upgrade your plan or buy more to add workspaces.",
                )

        await self._ensure_unique_name(payload.name.strip(), current_user, db)

        workspace = Workspace(
            user_id=current_user.id,
            name=payload.name.strip(),
            description=payload.description.strip() if payload.description else None,
            state="GATHERING_INFO",
            idea={
                "idea_title": None,
                "idea_description": None,
                "problem_statement": None,
                "industry": "general",
                "founder_validation_goal": "validate my idea",
                "geography": "global",
                "business_stage": "idea",
            },
            conversation_history=[],
            validation_result=None,
            is_delete=False,
            created_at=now,
            updated_at=now,
        )

        db.add(workspace)
        logger.info(f"Creating workspace for user: {current_user.id}")
        await db.flush()   # Populate `workspace.id` without committing yet.
        await db.refresh(workspace)

        logger.info(
            "Workspace created: id=%s name=%r user_id=%s",
            workspace.id, workspace.name, current_user.id,
        )
        return WorkspaceResponse.model_validate(workspace)

    # ── List ──────────────────────────────────────────────────────────────────

    async def list_workspaces(
        self,
        current_user: User,
        db: AsyncSession,
        is_delete: bool = False,
    ) -> WorkspaceListResponse:
        """Return all workspaces owned by current_user, filtered by is_delete."""
        result = await db.execute(
            select(Workspace)
            .where(Workspace.user_id == current_user.id, Workspace.is_delete == is_delete)
            .order_by(Workspace.created_at.desc())
        )
        workspaces = result.scalars().all()
        logger.info(f"Fetching the workspaces for user id: {current_user.id} with workspace count: {len(workspaces)}")
        return WorkspaceListResponse(
            total=len(workspaces),
            workspaces=[WorkspaceResponse.model_validate(w) for w in workspaces],
        )

    # ── Get by ID ─────────────────────────────────────────────────────────────

    async def get_workspace(
        self,
        workspace_id: int,
        current_user: User,
        db: AsyncSession,
    ) -> WorkspaceResponse:
        """Return a single workspace — 404 if not found, 403 if not owner."""
        workspace = await self._fetch_owned_workspace(workspace_id, current_user, db)
        return WorkspaceResponse.model_validate(workspace)

    # ── Update ────────────────────────────────────────────────────────────────

    async def update_workspace(
        self,
        workspace_id: int,
        payload: UpdateWorkspaceRequest,
        current_user: User,
        db: AsyncSession,
    ) -> WorkspaceResponse:
        """Update name and/or description of an owned workspace — 404/403 enforced."""
        workspace = await self._fetch_owned_workspace(workspace_id, current_user, db)
        logger.info(f"Updating workspace for user id: {current_user.id} and workspace id: {workspace_id}")
        new_name = payload.name.strip()
        if new_name != workspace.name:
            
            await self._ensure_unique_name(new_name, current_user, db, exclude_workspace_id=workspace_id)

    
        workspace.name = new_name
        workspace.description = payload.description.strip() if payload.description else None
        workspace.updated_at = datetime.now(timezone.utc)
        
        await db.flush()
        await db.refresh(workspace)

        logger.info(
            "Workspace updated: id=%s user_id=%s",
            workspace.id, current_user.id,
        )
        return WorkspaceResponse.model_validate(workspace)

    # ── Get by User ID ────────────────────────────────────────────────────────

    async def get_workspaces_by_user_id(
        self,
        user_id: int,
        current_user: User,
        db: AsyncSession,
        is_delete: bool = False,
    ) -> WorkspaceListResponse:
        """Return all workspaces for a given user_id, filtered by is_delete."""
        if user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to view workspaces for this user.",
            )

        result = await db.execute(
            select(Workspace)
            .where(Workspace.user_id == user_id, Workspace.is_delete == is_delete)
            .order_by(Workspace.created_at.desc())
        )
        workspaces = result.scalars().all()

        return WorkspaceListResponse(
            total=len(workspaces),
            workspaces=[WorkspaceResponse.model_validate(w) for w in workspaces],
        )

    # ── Delete (Archive) ─────────────────────────────────────────────────────

    async def delete_workspace(
        self,
        workspace_id: int,
        current_user: User,
        db: AsyncSession,
    ) -> DeleteWorkspaceResponse:
        """Soft-delete (archive) a workspace by setting is_delete=True — 404/403 enforced."""
        workspace = await self._fetch_owned_workspace(
            workspace_id, current_user, db, require_active=False
        )

        if workspace.is_delete:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Workspace {workspace_id} is already archived.",
            )

        workspace.is_delete = True
        workspace.updated_at = datetime.now(timezone.utc)

        await db.flush()
        logger.info(
            "Workspace archived: id=%s user_id=%s",
            workspace_id, current_user.id,
        )
        return DeleteWorkspaceResponse(workspace_id=workspace_id, is_delete=True)

    # ── Hard Delete ───────────────────────────────────────────────────────────

    async def hard_delete_workspace(
        self,
        workspace_id: int,
        current_user: User,
        db: AsyncSession,
    ) -> HardDeleteWorkspaceResponse:
        """Permanently delete a workspace and all its data — 404/403 enforced.

        Removes any S3-stored attachments (best-effort), then deletes the workspace
        row. Related workspace_attachments/surveys rows are removed via DB-level
        ON DELETE CASCADE.
        """
        workspace = await self._fetch_owned_workspace(
            workspace_id, current_user, db, require_active=False
        )

        result = await db.execute(
            select(WorkspaceAttachment).where(WorkspaceAttachment.workspace_id == workspace_id)
        )
        for attachment in result.scalars().all():
            s3_storage_service.delete_workspace_asset(attachment.s3_key)

        await db.delete(workspace)
        await db.flush()

        logger.info(
            "Workspace permanently deleted: id=%s user_id=%s",
            workspace_id, current_user.id,
        )
        return HardDeleteWorkspaceResponse(workspace_id=workspace_id)

    # ── Restore ───────────────────────────────────────────────────────────────

    async def restore_workspace(
        self,
        workspace_id: int,
        current_user: User,
        db: AsyncSession,
    ) -> RestoreWorkspaceResponse:
        """Restore an archived workspace by setting is_delete=False — 404/403 enforced."""
        workspace = await self._fetch_owned_workspace(
            workspace_id, current_user, db, require_active=False
        )

        if not workspace.is_delete:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Workspace {workspace_id} is not archived.",
            )

        workspace.is_delete = False
        workspace.updated_at = datetime.now(timezone.utc)

        await db.flush()
        logger.info(
            "Workspace restored: id=%s user_id=%s",
            workspace_id, current_user.id,
        )
        return RestoreWorkspaceResponse(workspace_id=workspace_id, is_delete=False)

    # ── Mentor Chat Sub-resource ──────────────────────────────────────────────

    async def process_mentor_chat(
        self,
        workspace_id: int,
        payload: WorkspaceChatRequest,
        current_user: User,
        db: AsyncSession,
    ) -> WorkspaceChatResponse:
        """Process AI Mentor message inside a workspace."""
        workspace = await self._fetch_owned_workspace(workspace_id, current_user, db)
        logger.info(
            "Mentor chat received: workspace_id=%s user_id=%s message_len=%s attachments=%s",
            workspace_id, current_user.id, len(payload.message or ""), len(payload.attachments or []),
        )

        ws_state = WorkspaceMentorState(
            workspace_id=str(workspace.id),
            state=workspace.state or "GATHERING_INFO",
            idea=workspace.idea or {},
            conversation_history=workspace.conversation_history or [],
            validation_result=workspace.validation_result
        )

        # Inject founder name into idea dict so mentor can use it for personalisation
        if "founder_name" not in ws_state.idea or not ws_state.idea.get("founder_name"):
            if current_user.display_name:
                ws_state.idea["founder_name"] = current_user.display_name.strip().split()[0].title()
            elif current_user.username:
                ws_state.idea["founder_name"] = current_user.username.split("@")[0].title()

        updated_state = await mentor_service.process_message(
            state=ws_state,
            user_message=payload.message,
            attachments=payload.attachments,
            user_id=current_user.id,
            db=db,
        )


        # Save back to database
        workspace.state = updated_state.state
        workspace.idea = updated_state.idea
        workspace.conversation_history = list(updated_state.conversation_history)
        workspace.validation_result = updated_state.validation_result
        workspace.updated_at = datetime.now(timezone.utc)

        await db.flush()
        await db.refresh(workspace)

        # Auto-sync agent-generated survey questions into the `surveys` table if available
        if workspace.validation_result:
            await survey_service.sync_survey_from_validation_result(
                user_id=current_user.id,
                workspace_id=workspace.id,
                validation_result=workspace.validation_result,
                db=db,
            )

        assistant_reply = "I'm listening. Tell me more!"
        if updated_state.conversation_history:
            for msg in reversed(updated_state.conversation_history):
                if msg.get("role") == "assistant":
                    assistant_reply = msg.get("content", "")
                    break

        logger.info(
            "Mentor chat processed: workspace_id=%s user_id=%s new_state=%s",
            workspace_id, current_user.id, workspace.state,
        )
        return WorkspaceChatResponse(
            reply=assistant_reply,
            workspace_id=workspace.id,
            state=workspace.state,
            idea=workspace.idea,
            validation_result=workspace.validation_result
        )

    # ── Workspace State Sub-resource ──────────────────────────────────────────

    async def get_workspace_state(
        self,
        workspace_id: int,
        current_user: User,
        db: AsyncSession,
    ) -> WorkspaceStateResponse:
        """Fetch complete workspace dialogue, idea context & validation result."""
        workspace = await self._fetch_owned_workspace(workspace_id, current_user, db)
        return WorkspaceStateResponse.model_validate(workspace)

    # ── Reset Mentor Sub-resource ─────────────────────────────────────────────

    async def reset_workspace_mentor(
        self,
        workspace_id: int,
        current_user: User,
        db: AsyncSession,
    ) -> WorkspaceStateResponse:
        """Reset conversation dialogue state for a workspace."""
        workspace = await self._fetch_owned_workspace(workspace_id, current_user, db)

        default_idea = {
            "idea_title": None,
            "idea_description": None,
            "problem_statement": None,
            "industry": "general",
            "founder_validation_goal": "validate my idea",
            "geography": "global",
            "business_stage": "idea",
        }

        # Personalise greeting with user's first name when available
        user_first_name = ""
        if current_user.display_name:
            user_first_name = current_user.display_name.strip().split()[0].title()
        elif current_user.username:
            user_first_name = current_user.username.split("@")[0].title()

        name_greeting = f"Hello {user_first_name}!" if user_first_name else "Hello!"
        initial_greeting = (
            f"{name_greeting} I'm Arya, your AI Mentor at Axiora Pulse. "
            "I'm here to help you validate your idea, challenge the right assumptions, and build a clear path forward — "
            "without wasting time or capital. "
            "Tell me about your startup idea and the problem you're solving, "
            "and let's work through this together."
        )

        workspace.state = "GATHERING_INFO"
        workspace.idea = default_idea
        workspace.conversation_history = [{"role": "assistant", "content": initial_greeting}]
        workspace.validation_result = None
        workspace.updated_at = datetime.now(timezone.utc)

        await db.flush()
        await db.refresh(workspace)

        logger.info(
            "Mentor dialogue reset: workspace_id=%s user_id=%s",
            workspace_id, current_user.id,
        )
        return WorkspaceStateResponse.model_validate(workspace)

    # ── Report Export Sub-resource ────────────────────────────────────────────

    async def export_workspace_report(
        self,
        workspace_id: int,
        agent_name: str,
        export_format: str,
        current_user: User,
        db: AsyncSession,
    ) -> Response:
        """Generate and download the template-based PDF report for a workspace."""
        workspace = await self._fetch_owned_workspace(workspace_id, current_user, db)

        if not workspace.validation_result:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Workspace {workspace_id} has not been validated yet. Please run validation first."
            )

        idea_info = workspace.idea or {"idea_title": workspace.name}

        file_bytes, media_type, filename = report_service.generate_report(
            agent_name=agent_name,
            validation_result=workspace.validation_result,
            idea_info=idea_info,
            export_format=export_format
        )
        logger.info(
            "Report exported: workspace_id=%s user_id=%s agent=%s format=%s",
            workspace_id, current_user.id, agent_name, export_format,
        )

        return Response(
            content=file_bytes,
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    # ── Certificate of Completion ─────────────────────────────────────────────

    async def generate_certificate(
        self,
        workspace_id: int,
        current_user: User,
        db: AsyncSession,
        display_name: str | None = None,
    ) -> Response:
        """Generate and download a Certificate of Completion for a validated workspace."""
        workspace = await self._fetch_owned_workspace(workspace_id, current_user, db)

        if not workspace.validation_result:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Workspace {workspace_id} has not been validated yet. Please run validation first."
            )

        if not display_name or not display_name.strip():
            display_name = current_user.display_name
            if not display_name:
                display_name = current_user.username.split("@")[0]
        display_name = display_name.strip().title()

        issue_datetime = self._certificate_issue_datetime(workspace)
        certificate_id = f"Pulse/{issue_datetime:%Y/%m}/{workspace.id:04d}"
        issue_date = issue_datetime.strftime("%d %B %Y")

        file_bytes = certificate_service.generate_certificate(
            display_name,
            certificate_id=certificate_id,
            issue_date=issue_date,
        )

        logger.info(
            "Certificate generated: workspace_id=%s user_id=%s name=%s",
            workspace_id, current_user.id, display_name,
        )

        safe_workspace_name = re.sub(r"[^A-Za-z0-9_-]+", "", workspace.name) or f"workspace_{workspace_id}"
        filename = f"idea_validation_certificate_{safe_workspace_name}.pdf"

        return Response(
            content=file_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    def _certificate_issue_datetime(self, workspace: Workspace) -> datetime:
        validation_result = workspace.validation_result or {}
        created_at = validation_result.get("created_at")

        if isinstance(created_at, datetime):
            return created_at

        if isinstance(created_at, str):
            try:
                return datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError:
                logger.warning(
                    "Unable to parse validation created_at for workspace_id=%s: %s",
                    workspace.id,
                    created_at,
                )

        return workspace.updated_at or datetime.now(timezone.utc)

    # ── Update Workspace Survey Questions (User Session) ──────────────────────

    async def update_workspace_survey_questions(
        self,
        workspace_id: int,
        payload: UpdateWorkspaceSurveyQuestionsRequest,
        current_user: User,
        db: AsyncSession,
    ) -> UpdateWorkspaceSurveyQuestionsResponse:
        """Allow regular users to edit survey questions in their active workspace session."""
        workspace = await self._fetch_owned_workspace(workspace_id, current_user, db)

        val_result = dict(workspace.validation_result or {})
        agent_results = dict(val_result.get("agent_results") or {})
        survey_agent_output = dict(agent_results.get("survey_intelligence_agent") or {})
        survey_data = dict(survey_agent_output.get("data") or {})

        # Convert question items to dict format
        updated_questions = [item.model_dump(exclude_unset=True) for item in payload.questions]
        survey_data["questions"] = updated_questions

        if payload.survey_title:
            survey_data["survey_title"] = payload.survey_title.strip()
        if payload.survey_objective:
            survey_data["survey_objective"] = payload.survey_objective.strip()

        # Update nested dict structure
        survey_agent_output["data"] = survey_data
        agent_results["survey_intelligence_agent"] = survey_agent_output
        val_result["agent_results"] = agent_results

        # Reassign to trigger SQLAlchemy mutation tracking
        workspace.validation_result = val_result
        workspace.updated_at = datetime.now(timezone.utc)

        await db.flush()
        await db.refresh(workspace)

        # Sync updated questions into `surveys` table
        await survey_service.sync_survey_from_validation_result(
            user_id=current_user.id,
            workspace_id=workspace.id,
            validation_result=workspace.validation_result,
            db=db,
        )

        logger.info(
            "Workspace %s survey questions updated by user_id=%s count=%s",
            workspace_id, current_user.id, len(updated_questions)
        )

        return UpdateWorkspaceSurveyQuestionsResponse(
            workspace_id=workspace.id,
            survey_title=survey_data.get("survey_title"),
            survey_objective=survey_data.get("survey_objective"),
            questions=updated_questions,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _ensure_unique_name(
        self,
        name: str,
        current_user: User,
        db: AsyncSession,
        exclude_workspace_id: Optional[int] = None,
    ) -> None:
        """Raise 409 if current_user already has a workspace with this name.

        Checks across all workspaces regardless of archive status — an archived
        workspace's title stays reserved until it is permanently deleted.
        """
        query = select(Workspace).where(
            Workspace.user_id == current_user.id,
            Workspace.name == name,
        )
        logger.info(f"Checking for unique name: {name} for user id: {current_user.id}")
        if exclude_workspace_id is not None:
            query = query.where(Workspace.id != exclude_workspace_id)
        result = await db.execute(query)
        if result.scalar_one_or_none() is not None:
            logger.warning(
                "Workspace conflict: user_id=%s already has a workspace named %r (exclude_id=%s)",
                current_user.id, name, exclude_workspace_id
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"You already have a workspace named '{name}'.",
            )

    async def _fetch_owned_workspace(
        self,
        workspace_id: int,
        current_user: User,
        db: AsyncSession,
        require_active: bool = True,
    ) -> Workspace:
        """Fetch a workspace and enforce ownership.

        By default, archived workspaces (is_delete=True) are treated as not found —
        callers that need to see archived workspaces (delete-already-archived check,
        restore, list-archived) must pass require_active=False explicitly.
        """
        result = await db.execute(
            select(Workspace).where(Workspace.id == workspace_id)
        )
        workspace = result.scalar_one_or_none()
        logger.info(f"Workspace fetched for id: {workspace_id}")
        if workspace is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workspace {workspace_id} not found.",
            )

        if workspace.user_id != current_user.id:
            logger.warning(
                "Unauthorized workspace access attempt: workspace_id=%s by user_id=%s (owner=%s)",
                workspace_id, current_user.id, workspace.user_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this workspace.",
            )

        if require_active and workspace.is_delete:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workspace {workspace_id} not found.",
            )

        return workspace


# ── Singleton ─────────────────────────────────────────────────────────────────
workspace_service = WorkspaceService()
