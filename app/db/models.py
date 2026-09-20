"""SQLAlchemy ORM models for users, workspace, agents, and orchestration system."""
from datetime import date, datetime
import uuid


from sqlalchemy import Boolean, CheckConstraint, Column, Date, DateTime, Float, ForeignKey, Index, Integer, String, Table, Text, UniqueConstraint, JSON

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.timezone import now_ist


class Base(DeclarativeBase):
    pass


class User(Base):
    """Persisted user record — never expose hashed password or raw OTP in API responses."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    username: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    # Nullable: federated (e.g. Google SSO) accounts have no local password.
    password: Mapped[str | None] = mapped_column(
        String(512), nullable=True
    )
    # How the account authenticates: "local" (email + password + OTP) or "google".
    auth_provider: Mapped[str] = mapped_column(
        String(20), nullable=False, default="local", server_default="local"
    )
    # Google's stable subject identifier ("sub" claim); set only for linked Google accounts.
    google_sub: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True, index=True
    )
    register_otp: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    register_otp_expiry: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    register_mfa: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    register_otp_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    forgot_password_otp: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    forgot_password_otp_expiry: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    login_otp: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    login_otp_expiry: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Role (many-to-one) ────────────────────────────────────────────────────
    role_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False, default=3
    )
    role: Mapped["Role"] = relationship("Role", back_populates="users", lazy="selectin")

    def has_role(self, role_name: str) -> bool:
        """Check if the user holds a specific role (e.g. 'admin', 'member', 'viewer')."""
        return self.role is not None and self.role.name == role_name

    @property
    def _primary_role(self) -> str:
        """Return the role name for backward-compatible response payloads."""
        return self.role.name if self.role else "viewer"

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r} role={self._primary_role!r}>"


class Role(Base):
    """Named role (admin, member, viewer) — one-to-many with User via role_id FK."""

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    users: Mapped[list["User"]] = relationship(
        "User", back_populates="role", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Role id={self.id} name={self.name!r}>"


class UserDetails(Base):
    __tablename__ = "user_details"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    profile_id: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    # Nullable: Google SSO provides no phone number; users add it later in their profile.
    mobile_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(20), nullable=True)
    profile_status: Mapped[str] = mapped_column(String(20), nullable=False, default="Active")
    nationality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    communication_preferences: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    last_login_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_ist
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_ist, onupdate=now_ist
    )

    def __repr__(self) -> str:
        return f"<UserDetails id={self.id} profile_id={self.profile_id!r} user_id={self.user_id}>"


class RefreshSession(Base):
    """A server-side record for a rotating refresh token session."""

    __tablename__ = "refresh_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )


class AuthActions(Base):
    """Per-user auth-gate flags returned on every successful regular-user login.

    payment              – True  → user has completed payment (default: True)
    interactive_questions – True → user has answered onboarding questions (default: True)
    """

    __tablename__ = "auth_actions"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True
    )
    payment: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    interactive_questions: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<AuthActions user_id={self.user_id} "
            f"payment={self.payment} interactive_questions={self.interactive_questions}>"
        )


class InteractiveQuestionnaire(Base):
    """Admin-defined question template for interactive questionnaires."""

    __tablename__ = "interactive_questionnaires"
    __table_args__ = (
        CheckConstraint(
            "answer_type IN ('textarea', 'radiobuttons', 'checkboxes', 'dropdown')",
            name="ck_interactive_questionnaires_answer_type",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )
    optional: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    answers: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<InteractiveQuestionnaire id={self.id} answer_type={self.answer_type!r}>"


class UserInteractiveQuestionnaire(Base):
    """Stores a user's responses to a questionnaire template."""

    __tablename__ = "user_interactive_questionnaires"
    __table_args__ = (
        Index(
            "ix_user_interactive_questionnaires_user_id_questionnaire_id",
            "user_id",
            "questionnaire_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    questionnaire_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("interactive_questionnaires.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_answers: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    submission_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<UserInteractiveQuestionnaire id={self.id} user_id={self.user_id} questionnaire_id={self.questionnaire_id}>"


class FeedbackQuestionnaire(Base):
    """Admin-defined question template for the feedback form."""

    __tablename__ = "feedback_questionnaires"
    __table_args__ = (
        CheckConstraint(
            "answer_type IN ('textarea', 'radiobuttons', 'checkboxes', 'dropdown', 'emoji')",
            name="ck_feedback_questionnaires_answer_type",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )
    optional: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    answers: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    is_display: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<FeedbackQuestionnaire id={self.id} answer_type={self.answer_type!r}>"


class UserFeedbackQuestionnaire(Base):
    """Stores a user's feedback responses to a feedback questionnaire template."""

    __tablename__ = "user_feedback_questionnaires"
    __table_args__ = (
        Index(
            "ix_user_feedback_questionnaires_user_id_workspace_id_questionnaire_id",
            "user_id",
            "workspace_id",
            "questionnaire_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    questionnaire_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("feedback_questionnaires.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_answers: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    submission_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<UserFeedbackQuestionnaire id={self.id} user_id={self.user_id} questionnaire_id={self.questionnaire_id}>"


class Workspace(Base):
    """
    Persisted Workspace record — scoped to a user.
    Owns workspace metadata, mentor conversation state, extracted idea details, and validation reports.
    """

    __tablename__ = "workspaces"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_workspaces_user_id_name"),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    description: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    state: Mapped[str] = mapped_column(
        String(50), nullable=False, default="GATHERING_INFO"
    )
    idea: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict
    )
    conversation_history: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    validation_result: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )
    is_delete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<Workspace id={self.id} name={self.name!r} user_id={self.user_id} state={self.state!r} is_delete={self.is_delete}>"


class WorkspaceAttachment(Base):
    """
    Persisted record of a file uploaded by a user into a workspace.
    Stores the S3 key and public URL for images, PDFs, and documents.
    Files are stored in axiora-assets under:
      Assets/users/{user_id}/workspaces/{workspace_id}/{type}/{uuid}_{filename}
    """

    __tablename__ = "workspace_attachments"
    __table_args__ = (
        Index("ix_workspace_attachments_workspace_id", "workspace_id"),
        Index("ix_workspace_attachments_user_id", "user_id"),
        CheckConstraint(
            "file_type IN ('image', 'pdf', 'doc')",
            name="ck_workspace_attachments_file_type",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    file_type: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True
    )  # 'image' | 'pdf' | 'doc'
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False, default="application/octet-stream")
    s3_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<WorkspaceAttachment id={self.id} file_name={self.file_name!r} "
            f"file_type={self.file_type!r} workspace_id={self.workspace_id}>"
        )


class Survey(Base):
    """Persisted survey record — owns a set of questions scoped to a user's workspace."""

    __tablename__ = "surveys"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    public_token: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True, default=lambda: uuid.uuid4().hex
    )
    survey_link: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    questions: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    analysis_result: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<Survey id={self.id} user_id={self.user_id} workspace_id={self.workspace_id}>"


class PublicSurveyResponse(Base):
    """Stores external user responses for a shareable public survey."""

    __tablename__ = "public_survey_responses"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    survey_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Mandatory respondent identity: full name + email; contact number is optional.
    respondent_name: Mapped[str] = mapped_column(String(255), nullable=False)
    respondent_email: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    answers: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<PublicSurveyResponse id={self.id} survey_id={self.survey_id}>"



# ── Section 11: Core Orchestration Data Models (8 Tables) ─────────────────────

class AgentDefinition(Base):
    """Stores metadata about each agent."""
    __tablename__ = "agent_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    skill_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    default_model: Mapped[str] = mapped_column(String(100), nullable=False, default="gemini-1.5-flash")
    output_schema: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class SkillLibrary(Base):
    """Stores reusable skills."""
    __tablename__ = "skills_library"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    skill_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    input_schema: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    output_schema: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    guardrails: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class OrchestrationRun(Base):
    """Stores each full orchestration run."""
    __tablename__ = "orchestration_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    workspace_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    idea_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    run_type: Mapped[str] = mapped_column(String(50), nullable=False, default="idea_validation")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    final_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_verdict: Mapped[str | None] = mapped_column(String(50), nullable=True)


class OrchestrationStep(Base):
    """Stores each step in an orchestration run."""
    __tablename__ = "orchestration_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    orchestration_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("orchestration_runs.id"), nullable=False, index=True)
    step_name: Mapped[str] = mapped_column(String(100), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class AgentRun(Base):
    """Stores each agent execution details."""
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    orchestration_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("orchestration_runs.id"), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(100), nullable=False)
    input_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    output_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success")
    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)
    token_input: Mapped[int] = mapped_column(Integer, default=0)
    token_output: Mapped[int] = mapped_column(Integer, default=0)
    cost_estimate: Mapped[float] = mapped_column(Float, default=0.0)


class MCPToolCallRecord(Base):
    """Stores tool usage requested via MCP."""
    __tablename__ = "mcp_tool_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    orchestration_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    agent_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    input_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    output_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success")
    execution_time_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class LLMUsageLogRecord(Base):
    """Stores LLM token usage and cost metrics."""
    __tablename__ = "llm_usage_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)
    request_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    response_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success")


class ValidationResultRecord(Base):
    """Stores final validation result per idea & orchestration run."""
    __tablename__ = "validation_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    idea_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    orchestration_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("orchestration_runs.id"), nullable=False, index=True)
    validation_score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_rating: Mapped[float] = mapped_column(Float, nullable=False)
    verdict: Mapped[str] = mapped_column(String(50), nullable=False)
    strengths: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    risks: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    assumptions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    recommendations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


# ══════════════════════════════════════════════════════════════════════════════
# Billing — Razorpay Subscriptions
# ══════════════════════════════════════════════════════════════════════════════

class Plan(Base):
    """
    Local subscription plan catalog. One row per sellable tier (e.g. pro, enterprise).

    A single local plan maps to up to two Razorpay Plan objects — one for the
    monthly billing cycle and one for the yearly cycle — because Razorpay encodes
    the interval and amount inside each Plan. `razorpay_plan_id_*` stay nullable
    until the matching plan is created in the Razorpay dashboard.

    Prices are stored in major currency units (whole rupees) to match the
    frontend `PricingPlan` contract (priceMonthly / priceYearly).
    """

    __tablename__ = "plans"
    __table_args__ = (
        UniqueConstraint("code", name="uq_plans_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # free | pro | enterprise
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    razorpay_plan_id_monthly: Mapped[str | None] = mapped_column(String(255), nullable=True)
    razorpay_plan_id_yearly: Mapped[str | None] = mapped_column(String(255), nullable=True)
    price_monthly: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price_yearly: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    features: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    tier: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # gating rank: free=0, pro=1, ...

    # ── Per-plan quota limits (Phase-1 entitlements) ──────────────────────────
    # Enforced by the billing/entitlement layer. For the integer caps, NULL means
    # "unlimited / not enforced". Storage, stage re-runs and analytics tiers are
    # intentionally not modelled yet — added when those features are scoped.
    workspace_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    survey_response_cap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    regeneration_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    export_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    popular: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")  # highlight in UI
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<Plan id={self.id} code={self.code!r} tier={self.tier}>"


class UserAllowedWorkspaces(Base):
    """Per-user accumulating entitlements (issues #191/#192/#194).

    A running allowance that only ever grows: free baseline (1 workspace / 100
    responses), plus each paid Razorpay charge adds the plan's amounts on top
    (Builder +3 / +500, Pro +10 / +2000), including on every monthly renewal.
    There is no decrement. Only the two limits enforced today live here; export
    stays a per-plan on/off, and storage/regenerations/analytics are not built.
    """

    __tablename__ = "user_allowed_workspaces"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_allowed_workspaces_user_id"),
        Index("ix_user_allowed_workspaces_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    allowed_workspaces: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    allowed_responses: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default="100")
    # When the free (Starter) 7-day trial ends. NULL = no trial started (paid-first
    # users rely on their subscription). Set on free-plan selection.
    free_trial_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return (
            f"<UserAllowedWorkspaces user_id={self.user_id} "
            f"workspaces={self.allowed_workspaces} responses={self.allowed_responses}>"
        )


class Subscription(Base):
    """
    A user's subscription to a Razorpay plan.

    Status mirrors Razorpay's subscription lifecycle and is updated authoritatively
    by the webhook handler — never trust the browser callback as source of truth.
    Statuses: created | authenticated | active | pending | halted | cancelled |
              completed | expired
    """

    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("razorpay_subscription_id", name="uq_subscriptions_rzp_sub_id"),
        Index("ix_subscriptions_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    plan_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("plans.id", ondelete="SET NULL"), nullable=True
    )
    razorpay_subscription_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    razorpay_plan_id: Mapped[str] = mapped_column(String(255), nullable=False)
    billing_period: Mapped[str] = mapped_column(String(10), nullable=False, default="monthly")  # monthly | yearly
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="created", index=True)
    short_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<Subscription id={self.id} user_id={self.user_id} status={self.status!r}>"


class Payment(Base):
    """
    Audit record of an individual Razorpay payment/charge against a subscription.
    Written from the `subscription.charged` / `payment.*` webhooks for reconciliation.
    Amounts are stored in the smallest currency unit (paise), as Razorpay reports them.
    """

    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("razorpay_payment_id", name="uq_payments_rzp_payment_id"),
        Index("ix_payments_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    subscription_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True
    )
    razorpay_payment_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    razorpay_invoice_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # paise
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<Payment id={self.id} rzp={self.razorpay_payment_id!r} status={self.status!r}>"


class WebhookEvent(Base):
    """
    Idempotency ledger for incoming Razorpay webhooks.

    Every webhook carries an `X-Razorpay-Event-Id` header. We insert that id here
    before processing; a duplicate delivery collides on the unique constraint and
    is skipped, so retried webhooks never double-apply.
    """

    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_webhook_events_event_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<WebhookEvent id={self.id} type={self.event_type!r} processed={self.processed}>"


# ══════════════════════════════════════════════════════════════════════════════
# Token Tracking — Per-User & Per-Workspace Token Metrics
# ══════════════════════════════════════════════════════════════════════════════

class TokenUsage(Base):
    """
    Audit and analytics ledger for all LLM token consumption.
    Tracks input/output tokens, cost, provider, model, user, and workspace context.
    """

    __tablename__ = "token_usages"
    __table_args__ = (
        Index("ix_token_usages_user_created", "user_id", "created_at"),
        Index("ix_token_usages_workspace_created", "workspace_id", "created_at"),
        Index("ix_token_usages_user_workspace", "user_id", "workspace_id"),
        Index("ix_token_usages_source", "source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # mentor_chat | idea_extraction | agent_execution | survey_generation | survey_analysis
    agent_name: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="openai")
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<TokenUsage id={self.id} user_id={self.user_id} workspace_id={self.workspace_id} "
            f"model={self.model!r} total_tokens={self.total_tokens} cost={self.estimated_cost}>"
        )


class UserTokenTotal(Base):
    """
    Cumulative total token consumption per user (exactly 1 row per user).
    Maintains real-time aggregated input, output, total tokens, total cost, and total calls.
    """

    __tablename__ = "user_token_totals"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_token_totals_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<UserTokenTotal user_id={self.user_id} prompt={self.prompt_tokens} "
            f"completion={self.completion_tokens} total={self.total_tokens} cost={self.total_cost}>"
        )


