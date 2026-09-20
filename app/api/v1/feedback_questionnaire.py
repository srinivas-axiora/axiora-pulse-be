import logging

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.db.models import User
from app.models.feedback_questionnaire_models import (
    CreateFeedbackQuestionRequest,
    FeedbackQuestionnaireListResponse,
    FeedbackQuestionnaireResponse,
    UpdateFeedbackQuestionRequest,
)
from app.services.feedback_questionnaire_service import feedback_questionnaire_service

admin_router = APIRouter(prefix="/admin/feedback-questionnaire", tags=["Feedback Questionnaire"])
user_router = APIRouter(prefix="/feedback-questionnaire", tags=["Feedback Questionnaire"])
logger = logging.getLogger(__name__)


@admin_router.post(
    "",
    response_model=FeedbackQuestionnaireResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a feedback question",
    description="Allows administrators to create a new questionnaire question shown on the feedback form.",
)
async def create_feedback_question(
    payload: CreateFeedbackQuestionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FeedbackQuestionnaireResponse:
    return await feedback_questionnaire_service.create_question(payload, current_user, db)


@admin_router.get(
    "",
    response_model=list[FeedbackQuestionnaireResponse],
    status_code=status.HTTP_200_OK,
    summary="List all feedback questions",
    description="Returns all feedback questions ordered by ID ascending, regardless of display flag.",
)
async def list_feedback_questions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[FeedbackQuestionnaireResponse]:
    logger.info("Listing all feedback questions for user_id=%s", current_user.id)
    return await feedback_questionnaire_service.list_questions(db)


@admin_router.put(
    "/{question_id}",
    response_model=FeedbackQuestionnaireResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a feedback question",
    description="Allows administrators to update an existing questionnaire question shown on the feedback form.",
)
async def update_feedback_question(
    payload: UpdateFeedbackQuestionRequest,
    question_id: int = Path(..., ge=1, description="ID of the feedback question to update"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FeedbackQuestionnaireResponse:
    return await feedback_questionnaire_service.update_question(question_id, payload, current_user, db)


@user_router.get(
    "",
    response_model=FeedbackQuestionnaireListResponse,
    status_code=status.HTTP_200_OK,
    summary="List displayed feedback questions for a workspace",
    description=(
        "Returns the active questions shown on the feedback form. Pass `is_display=false` to "
        "list hidden ones. When a `workspace_id` is provided, `alreadySubmitted` tells whether "
        "feedback was already given for that workspace; it is null otherwise (generic fetch)."
    ),
)
async def list_displayed_feedback_questions(
    workspace_id: int | None = Query(None, ge=1, description="Optional workspace to check submission status for"),
    is_display: bool = Query(True, description="Only return questions with this display flag"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FeedbackQuestionnaireListResponse:
    logger.info("Listing displayed feedback questions for user_id=%s", current_user.id)
    questions = await feedback_questionnaire_service.list_displayed_questions(db, is_display)
    already_submitted = None
    if workspace_id is not None:
        already_submitted = await feedback_questionnaire_service.has_user_submitted(
            current_user.id, workspace_id, db
        )
    return FeedbackQuestionnaireListResponse(alreadySubmitted=already_submitted, questions=questions)