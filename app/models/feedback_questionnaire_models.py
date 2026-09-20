"""Request and response schemas for the feedback questionnaire feature."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class CreateFeedbackQuestionRequest(BaseModel):
    """Payload for POST /api/v1/admin/feedback-questionnaire."""

    question: str = Field(..., min_length=1, description="Feedback question text")
    answer_type: Literal["textarea", "radiobuttons", "checkboxes", "dropdown", "emoji"] = Field(
        ..., description="Question answer input style"
    )
    optional: bool = Field(default=False, description="Whether the question is optional")
    answers: list[str] = Field(default_factory=list, description="Answer options for choice-based questions")
    is_display: bool = Field(default=True, description="Whether the question should appear on the feedback form")

    @model_validator(mode="after")
    def validate_answers_for_choice_types(self) -> "CreateFeedbackQuestionRequest":
        if self.answer_type in {"radiobuttons", "checkboxes", "dropdown", "emoji"} and len(self.answers) < 2:
            raise ValueError("Choice-based questions require at least 2 answers.")
        return self


class UpdateFeedbackQuestionRequest(BaseModel):
    """Payload for PUT /api/v1/admin/feedback-questionnaire/{question_id}."""

    question: str | None = Field(default=None, min_length=1, description="Updated feedback question text")
    answer_type: Literal["textarea", "radiobuttons", "checkboxes", "dropdown", "emoji"] | None = Field(
        default=None, description="Updated question answer input style"
    )
    optional: bool | None = Field(default=None, description="Whether the question is optional")
    answers: list[str] | None = Field(default=None, description="Updated answer options for choice-based questions")
    is_display: bool | None = Field(default=None, description="Whether the question should appear on the feedback form")


class FeedbackQuestionnaireResponse(BaseModel):
    """Response returned after a feedback question is created, updated or listed."""

    id: int
    question: str
    answer_type: str
    optional: bool
    answers: list[str]
    is_display: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class FeedbackQuestionnaireListResponse(BaseModel):
    """Feedback form payload for a workspace.

    Wraps the displayed questions together with the one-time submission flag.
    ``alreadySubmitted`` is ``None`` when no ``workspace_id`` was supplied (plain
    generic question listing) and a boolean when a workspace context is present.
    """

    alreadySubmitted: bool | None = Field(
        default=None,
        description="True when the user already submitted feedback for this workspace; null when no workspace_id was given",
    )
    questions: list[FeedbackQuestionnaireResponse] = Field(..., description="Questions shown on the feedback form")


class FeedbackAnswerItem(BaseModel):
    """Payload item for submitting feedback answers."""

    questionnaire_id: int = Field(..., ge=1, description="Feedback questionnaire template identifier")
    user_answers: list[str] = Field(default_factory=list, description="User answers for the feedback question")

    @field_validator("user_answers")
    @classmethod
    def validate_user_answers(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item is not None and str(item).strip() != ""]


class UserFeedbackSubmitRequest(BaseModel):
    """Payload for POST /api/v1/user-feedback."""

    workspace_id: int = Field(..., ge=1, description="Workspace the user completed, used for the certificate export")
    answers: list[FeedbackAnswerItem] = Field(default_factory=list, description="Feedback answers for the questionnaire")

    @field_validator("answers")
    @classmethod
    def validate_answers(cls, value: list[FeedbackAnswerItem]) -> list[FeedbackAnswerItem]:
        if len({item.questionnaire_id for item in value}) != len(value):
            raise ValueError("Duplicate questionnaire_id entries are not allowed.")
        return value


class UserFeedbackSubmitResponse(BaseModel):
    """Response returned after saving feedback answers."""

    message: str


class AdminUserFeedbackItem(BaseModel):
    """A single submitted feedback row for the administrator directory."""

    id: int
    user_id: int
    user_email: str
    user_display_name: str | None
    workspace_id: int | None
    questionnaire_id: int
    question: str | None
    user_answers: list[str]
    submission_date: datetime


class AdminUserFeedbackPagination(BaseModel):
    total: int
    limit: int
    offset: int


class AdminUserFeedbackListResponse(BaseModel):
    feedback: list[AdminUserFeedbackItem]
    pagination: AdminUserFeedbackPagination