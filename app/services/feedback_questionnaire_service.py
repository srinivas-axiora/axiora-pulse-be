import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, Response, status
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FeedbackQuestionnaire, User, UserFeedbackQuestionnaire
from app.models.feedback_questionnaire_models import (
    AdminUserFeedbackItem,
    AdminUserFeedbackListResponse,
    AdminUserFeedbackPagination,
    CreateFeedbackQuestionRequest,
    FeedbackAnswerItem,
    FeedbackQuestionnaireResponse,
    UpdateFeedbackQuestionRequest,
    UserFeedbackSubmitRequest,
)
from app.services.workspace_service import workspace_service

logger = logging.getLogger(__name__)


class FeedbackQuestionnaireService:
    async def list_questions(self, db: AsyncSession) -> list[FeedbackQuestionnaireResponse]:
        """Retrieve all feedback questions (admin view, regardless of display flag)."""
        result = await db.execute(
            select(FeedbackQuestionnaire).order_by(FeedbackQuestionnaire.id.asc())
        )
        questions = result.scalars().all()
        logger.info("Fetched %s feedback questions", len(questions))
        return [FeedbackQuestionnaireResponse.model_validate(q) for q in questions]

    async def list_displayed_questions(
        self, db: AsyncSession, is_display: bool = True
    ) -> list[FeedbackQuestionnaireResponse]:
        """Retrieve feedback questions filtered by the display flag (user view)."""
        result = await db.execute(
            select(FeedbackQuestionnaire)
            .where(FeedbackQuestionnaire.is_display.is_(is_display))
            .order_by(FeedbackQuestionnaire.id.asc())
        )
        questions = result.scalars().all()
        logger.info("Fetched %s displayed feedback questions (is_display=%s)", len(questions), is_display)
        return [FeedbackQuestionnaireResponse.model_validate(q) for q in questions]

    async def create_question(
        self,
        payload: CreateFeedbackQuestionRequest,
        current_user: User,
        db: AsyncSession,
    ) -> FeedbackQuestionnaireResponse:
        """Persist a new feedback question for admins."""
        if not current_user.has_role("admin"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin privileges required.",
            )

        now = datetime.now(timezone.utc)
        question = FeedbackQuestionnaire(
            question=payload.question.strip(),
            answer_type=payload.answer_type,
            optional=payload.optional,
            answers=list(payload.answers),
            is_display=payload.is_display,
            created_at=now,
            updated_at=now,
        )

        db.add(question)
        await db.flush()
        await db.refresh(question)

        logger.info(
            "Feedback question created: id=%s by admin_user_id=%s",
            question.id,
            current_user.id,
        )
        return FeedbackQuestionnaireResponse.model_validate(question)

    async def update_question(
        self,
        question_id: int,
        payload: UpdateFeedbackQuestionRequest,
        current_user: User,
        db: AsyncSession,
    ) -> FeedbackQuestionnaireResponse:
        """Update an existing feedback question for admins."""
        if not current_user.has_role("admin"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin privileges required.",
            )

        question = await db.get(FeedbackQuestionnaire, question_id)
        if question is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Feedback question not found.",
            )

        if payload.question is not None:
            question.question = payload.question.strip()
        if payload.answer_type is not None:
            question.answer_type = payload.answer_type
        if payload.optional is not None:
            question.optional = payload.optional
        if payload.answers is not None:
            question.answers = list(payload.answers)
        if payload.is_display is not None:
            question.is_display = payload.is_display

        question.updated_at = datetime.now(timezone.utc)
        await db.flush()
        await db.refresh(question)

        logger.info(
            "Feedback question updated: id=%s by admin_user_id=%s",
            question_id,
            current_user.id,
        )
        return FeedbackQuestionnaireResponse.model_validate(question)

    async def submit_feedback(
        self,
        payload: UserFeedbackSubmitRequest,
        current_user: User,
        db: AsyncSession,
    ) -> Response:
        """Persist a user's feedback answers and trigger the certificate export.

        Feedback is a one-time submission: existing answers for the same
        (user, workspace, question) are updated instead of duplicated. After
        the answers are saved, the workspace certificate export is triggered
        automatically and the generated PDF is returned as the response.
        """
        if not payload.answers:
            logger.warning(
                "Feedback submission rejected for user_id=%s: empty answers", current_user.id
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one feedback answer item is required.",
            )

        questions_result = await db.execute(select(FeedbackQuestionnaire))
        all_questions = questions_result.scalars().all()
        question_map = {q.id: q for q in all_questions}

        payload_map = {}
        for item in payload.answers:
            if item.questionnaire_id not in question_map:
                logger.warning(
                    "Feedback submission failed for user_id=%s: question id %s not found",
                    current_user.id,
                    item.questionnaire_id,
                )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Feedback question(s) not found: {[item.questionnaire_id]}",
                )
            if not question_map[item.questionnaire_id].is_display:
                logger.warning(
                    "Feedback submission failed for user_id=%s: hidden question %s submitted",
                    current_user.id,
                    item.questionnaire_id,
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Question {item.questionnaire_id} is not displayed on the feedback form.",
                )
            payload_map[item.questionnaire_id] = item

        for q_id, question in question_map.items():
            if not question.is_display:
                continue
            item = payload_map.get(q_id)
            if not question.optional:
                if item is None:
                    logger.warning(
                        "Feedback submission failed for user_id=%s: mandatory question %s missing",
                        current_user.id,
                        q_id,
                    )
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Question {q_id} requires a submission entry.",
                    )
                if not item.user_answers:
                    logger.warning(
                        "Feedback submission failed for user_id=%s: mandatory question %s missing answer",
                        current_user.id,
                        q_id,
                    )
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Question {q_id} requires at least one non-empty answer.",
                    )
            if item is not None:
                self._validate_question_answers(question, item, q_id, current_user.id)

        existing_result = await db.execute(
            select(UserFeedbackQuestionnaire).where(
                UserFeedbackQuestionnaire.user_id == current_user.id,
                UserFeedbackQuestionnaire.workspace_id == payload.workspace_id,
            )
        )
        existing_map = {
            row.questionnaire_id: row for row in existing_result.scalars().all()
        }

        try:
            for q_id, item in payload_map.items():
                now = datetime.now(timezone.utc)
                if q_id in existing_map:
                    record = existing_map[q_id]
                    record.user_answers = list(item.user_answers)
                    record.question_snapshot = question_map[q_id].question
                    record.submission_date = now
                    record.updated_at = now
                else:
                    record = UserFeedbackQuestionnaire(
                        user_id=current_user.id,
                        workspace_id=payload.workspace_id,
                        questionnaire_id=q_id,
                        user_answers=list(item.user_answers),
                        question_snapshot=question_map[q_id].question,
                        submission_date=now,
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(record)
            await db.flush()
        except Exception:
            logger.exception(
                "Database transaction failed while saving feedback for user_id=%s",
                current_user.id,
            )
            raise

        logger.info(
            "Feedback answers submitted: user_id=%s workspace_id=%s count=%s",
            current_user.id,
            payload.workspace_id,
            len(payload_map),
        )

        # Trigger the certificate export after the feedback is saved. The
        # generated PDF is returned to the client as a downloadable attachment.
        return await workspace_service.generate_certificate(
            payload.workspace_id, current_user, db
        )

    def _validate_question_answers(
        self,
        question: FeedbackQuestionnaire,
        item: FeedbackAnswerItem,
        q_id: int,
        user_id: int,
    ) -> None:
        """Validate answer rules for a single feedback question."""
        if question.answer_type in {"radiobuttons", "dropdown", "emoji"} and len(item.user_answers) > 1:
            logger.warning(
                "Feedback submission failed for user_id=%s: single-choice question %s has multiple answers",
                user_id,
                q_id,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Question {q_id} only accepts a single choice answer.",
            )

        if question.answer_type in {"radiobuttons", "dropdown", "checkboxes", "emoji"}:
            allowed_options = set(question.answers)
            for ans in item.user_answers:
                if ans not in allowed_options:
                    logger.warning(
                        "Feedback submission failed for user_id=%s: answer %r not allowed for question %s",
                        user_id,
                        ans,
                        q_id,
                    )
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Answer '{ans}' is not a valid option for question {q_id}.",
                    )

    async def list_user_feedback(
        self,
        db: AsyncSession,
        limit: int,
        offset: int,
        search: str | None,
        user_id: int | None,
        date_from: Optional[datetime],
        date_to: Optional[datetime],
    ) -> AdminUserFeedbackListResponse:
        """Return submitted feedback for the administrator directory with filters."""
        filters = []
        if user_id is not None:
            filters.append(UserFeedbackQuestionnaire.user_id == user_id)
        if search:
            term = f"%{search.strip()}%"
            filters.append(
                or_(
                    FeedbackQuestionnaire.question.ilike(term),
                    UserFeedbackQuestionnaire.question_snapshot.ilike(term),
                    cast(UserFeedbackQuestionnaire.user_answers, String).ilike(term),
                )
            )
        if date_from is not None:
            filters.append(UserFeedbackQuestionnaire.submission_date >= date_from)
        if date_to is not None:
            filters.append(UserFeedbackQuestionnaire.submission_date <= date_to)

        # The pagination total is the number of USERS who submitted feedback, not
        # the number of answered-question rows (one user submitting N questions is
        # still a single response).
        total_statement = (
            select(func.count(func.distinct(UserFeedbackQuestionnaire.user_id)))
            .join(User, User.id == UserFeedbackQuestionnaire.user_id)
            .outerjoin(
                FeedbackQuestionnaire,
                FeedbackQuestionnaire.id == UserFeedbackQuestionnaire.questionnaire_id,
            )
            .where(*filters)
        )
        rows_statement = (
            select(
                UserFeedbackQuestionnaire,
                User.username,
                User.display_name,
                FeedbackQuestionnaire.question,
            )
            .join(User, User.id == UserFeedbackQuestionnaire.user_id)
            .outerjoin(
                FeedbackQuestionnaire,
                FeedbackQuestionnaire.id == UserFeedbackQuestionnaire.questionnaire_id,
            )
            .where(*filters)
            .order_by(
                UserFeedbackQuestionnaire.submission_date.desc(),
                UserFeedbackQuestionnaire.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )

        total = (await db.execute(total_statement)).scalar_one()
        rows = (await db.execute(rows_statement)).all()
        logger.info(
            "Admin feedback fetched: user_id=%s count=%s total=%s",
            user_id,
            len(rows),
            total,
        )

        items = [
            AdminUserFeedbackItem(
                id=row.id,
                user_id=row.user_id,
                user_email=user_email,
                user_display_name=user_display_name,
                workspace_id=row.workspace_id,
                questionnaire_id=row.questionnaire_id,
                # Show the question text the respondent actually saw at submission
                # time; falls back to the template text for legacy pre-snapshot rows.
                question=row.question_snapshot or question_text,
                user_answers=list(row.user_answers),
                submission_date=row.submission_date,
            )
            for row, user_email, user_display_name, question_text in rows
        ]

        return AdminUserFeedbackListResponse(
            feedback=items,
            pagination=AdminUserFeedbackPagination(total=total, limit=limit, offset=offset),
        )


feedback_questionnaire_service = FeedbackQuestionnaireService()