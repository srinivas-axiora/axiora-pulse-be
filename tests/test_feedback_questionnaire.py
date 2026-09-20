import pytest
from datetime import datetime
from fastapi import status
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.db.models import FeedbackQuestionnaire, User, UserFeedbackQuestionnaire, Workspace
from app.models.feedback_questionnaire_models import CreateFeedbackQuestionRequest, UserFeedbackSubmitRequest


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


async def create_workspace(
    db_session: AsyncSession,
    *,
    user_id: int,
    name: str,
    validation_result: dict | None = None,
) -> Workspace:
    workspace = Workspace(
        user_id=user_id,
        name=name,
        state="VALIDATED" if validation_result else "GATHERING_INFO",
        idea={"idea_title": name},
        conversation_history=[],
        validation_result=validation_result,
    )
    db_session.add(workspace)
    await db_session.flush()
    await db_session.refresh(workspace)
    return workspace


async def seed_feedback_question(
    db_session: AsyncSession,
    *,
    question: str,
    answer_type: str,
    optional: bool = False,
    answers: list | None = None,
    is_display: bool = True,
) -> FeedbackQuestionnaire:
    q = FeedbackQuestionnaire(
        question=question,
        answer_type=answer_type,
        optional=optional,
        answers=answers or [],
        is_display=is_display,
    )
    db_session.add(q)
    await db_session.flush()
    await db_session.refresh(q)
    return q


async def _authenticate(user: User):
    from main import app

    async def _mock_current_user():
        return user

    app.dependency_overrides[get_current_user] = _mock_current_user


# ──────────────────────────────────────────────────────────────────────────────
# Pydantic Model Validation
# ──────────────────────────────────────────────────────────────────────────────

def test_create_feedback_question_choice_validation():
    req = CreateFeedbackQuestionRequest(
        question="How was your experience?",
        answer_type="textarea",
        optional=True,
        answers=[],
        is_display=True,
    )
    assert req.question == "How was your experience?"
    assert req.is_display is True

    with pytest.raises(ValueError) as exc:
        CreateFeedbackQuestionRequest(
            question="Choose one?",
            answer_type="radiobuttons",
            optional=False,
            answers=["One"],
        )
    assert "Choice-based questions require at least 2 answers." in str(exc.value)


def test_create_feedback_question_emoji_type_validation():
    req = CreateFeedbackQuestionRequest(
        question="How would you rate us?",
        answer_type="emoji",
        optional=False,
        answers=["\U0001F641", "\U0001F610", "\U0001F600"],
    )
    assert req.answer_type == "emoji"
    assert req.answers == ["\U0001F641", "\U0001F610", "\U0001F600"]

    with pytest.raises(ValueError) as exc:
        CreateFeedbackQuestionRequest(
            question="How would you rate us?",
            answer_type="emoji",
            optional=False,
            answers=["\U0001F600"],
        )
    assert "Choice-based questions require at least 2 answers." in str(exc.value)


def test_user_feedback_submit_request_validation():
    payload = UserFeedbackSubmitRequest(
        workspace_id=1,
        answers=[
            {"questionnaire_id": 1, "user_answers": ["  Hello  ", "", "World"]},
        ],
    )
    assert payload.answers[0].user_answers == ["Hello", "World"]

    with pytest.raises(ValueError) as exc:
        UserFeedbackSubmitRequest(
            workspace_id=1,
            answers=[
                {"questionnaire_id": 1, "user_answers": ["A"]},
                {"questionnaire_id": 1, "user_answers": ["B"]},
            ],
        )
    assert "Duplicate questionnaire_id" in str(exc.value)


# ──────────────────────────────────────────────────────────────────────────────
# Admin Create Feedback Question
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_feedback_question_success(client: AsyncClient, admin_user: User):
    await _authenticate(admin_user)
    payload = {
        "question": "Rate your experience",
        "answer_type": "dropdown",
        "optional": False,
        "answers": ["Excellent", "Good", "Average"],
        "is_display": True,
    }
    response = await client.post("/api/v1/admin/feedback-questionnaire", json=payload)
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["question"] == "Rate your experience"
    assert data["answer_type"] == "dropdown"
    assert data["answers"] == ["Excellent", "Good", "Average"]
    assert data["optional"] is False
    assert data["is_display"] is True
    assert "id" in data


@pytest.mark.asyncio
async def test_create_feedback_question_non_admin_forbidden(client: AsyncClient, normal_user: User):
    await _authenticate(normal_user)
    payload = {
        "question": "Any feedback?",
        "answer_type": "textarea",
        "optional": True,
        "answers": [],
    }
    response = await client.post("/api/v1/admin/feedback-questionnaire", json=payload)
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"] == "Admin privileges required."


# ──────────────────────────────────────────────────────────────────────────────
# Admin List & Update Feedback Questions
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_feedback_questions_admin(
    client: AsyncClient, normal_user: User, db_session: AsyncSession
):
    q1 = await seed_feedback_question(db_session, question="Q1", answer_type="textarea", optional=True, is_display=True)
    q2 = await seed_feedback_question(db_session, question="Q2", answer_type="dropdown", optional=False, answers=["A", "B"], is_display=False)
    await db_session.commit()

    await _authenticate(normal_user)
    response = await client.get("/api/v1/admin/feedback-questionnaire")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 2
    ids = [q["id"] for q in data]
    assert q1.id in ids and q2.id in ids


@pytest.mark.asyncio
async def test_update_feedback_question_success(
    client: AsyncClient, admin_user: User, db_session: AsyncSession
):
    q = await seed_feedback_question(db_session, question="Original", answer_type="textarea", optional=True)
    await db_session.commit()

    await _authenticate(admin_user)
    response = await client.put(
        f"/api/v1/admin/feedback-questionnaire/{q.id}",
        json={"question": "Updated", "optional": False, "is_display": False},
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["question"] == "Updated"
    assert data["optional"] is False
    assert data["is_display"] is False
    assert data["answer_type"] == "textarea"


@pytest.mark.asyncio
async def test_update_feedback_question_not_found(client: AsyncClient, admin_user: User):
    await _authenticate(admin_user)
    response = await client.put(
        "/api/v1/admin/feedback-questionnaire/999",
        json={"question": "Doesn't matter"},
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Feedback question not found."


@pytest.mark.asyncio
async def test_update_feedback_question_non_admin_forbidden(client: AsyncClient, normal_user: User):
    await _authenticate(normal_user)
    response = await client.put(
        "/api/v1/admin/feedback-questionnaire/1",
        json={"question": "Doesn't matter"},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


# ──────────────────────────────────────────────────────────────────────────────
# User Displayed Questions
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_displayed_feedback_questions(
    client: AsyncClient, normal_user: User, db_session: AsyncSession
):
    await seed_feedback_question(db_session, question="Shown", answer_type="textarea", optional=True, is_display=True)
    await seed_feedback_question(db_session, question="Hidden", answer_type="textarea", optional=True, is_display=False)
    await db_session.commit()

    await _authenticate(normal_user)
    response = await client.get("/api/v1/feedback-questionnaire?is_display=true")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 1
    assert data[0]["question"] == "Shown"

    response_hidden = await client.get("/api/v1/feedback-questionnaire?is_display=false")
    assert response_hidden.status_code == status.HTTP_200_OK
    assert len(response_hidden.json()) == 1
    assert response_hidden.json()[0]["question"] == "Hidden"


# ──────────────────────────────────────────────────────────────────────────────
# Submit User Feedback (+ auto certificate export)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_submit_feedback_empty_answers(client: AsyncClient, normal_user: User):
    await _authenticate(normal_user)
    response = await client.post(
        "/api/v1/user-feedback",
        json={"workspace_id": 1, "answers": []},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "At least one feedback answer item is required."


@pytest.mark.asyncio
async def test_submit_feedback_nonexistent_question(client: AsyncClient, normal_user: User):
    await _authenticate(normal_user)
    response = await client.post(
        "/api/v1/user-feedback",
        json={"workspace_id": 1, "answers": [{"questionnaire_id": 999, "user_answers": ["x"]}]},
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "Feedback question(s) not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_submit_feedback_hidden_question(client: AsyncClient, normal_user: User, db_session: AsyncSession):
    q = await seed_feedback_question(db_session, question="Hidden", answer_type="textarea", optional=False, is_display=False)
    await db_session.commit()

    await _authenticate(normal_user)
    response = await client.post(
        "/api/v1/user-feedback",
        json={"workspace_id": 1, "answers": [{"questionnaire_id": q.id, "user_answers": ["x"]}]},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "not displayed on the feedback form" in response.json()["detail"]


@pytest.mark.asyncio
async def test_submit_feedback_missing_mandatory_question(
    client: AsyncClient, normal_user: User, db_session: AsyncSession
):
    q_mandatory = await seed_feedback_question(db_session, question="Req", answer_type="textarea", optional=False)
    q_optional = await seed_feedback_question(db_session, question="Opt", answer_type="textarea", optional=True)
    await db_session.commit()
    mandatory_id, optional_id = q_mandatory.id, q_optional.id

    await _authenticate(normal_user)
    response = await client.post(
        "/api/v1/user-feedback",
        json={
            "workspace_id": 1,
            "answers": [{"questionnaire_id": optional_id, "user_answers": ["Optional"]}],
        },
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert f"Question {mandatory_id} requires a submission entry." in response.json()["detail"]


@pytest.mark.asyncio
async def test_submit_feedback_invalid_choice_option(
    client: AsyncClient, normal_user: User, db_session: AsyncSession
):
    q = await seed_feedback_question(db_session, question="Pick one", answer_type="dropdown", optional=False, answers=["A", "B"])
    await db_session.commit()

    await _authenticate(normal_user)
    response = await client.post(
        "/api/v1/user-feedback",
        json={"workspace_id": 1, "answers": [{"questionnaire_id": q.id, "user_answers": ["C"]}]},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "is not a valid option for question" in response.json()["detail"]


@pytest.mark.asyncio
async def test_submit_feedback_emoji_single_choice_rejected(
    client: AsyncClient, normal_user: User, db_session: AsyncSession
):
    q = await seed_feedback_question(
        db_session,
        question="Rate us",
        answer_type="emoji",
        optional=False,
        answers=["\U0001F641", "\U0001F610", "\U0001F600"],
    )
    workspace = await create_workspace(
        db_session, user_id=normal_user.id, name="Idea", validation_result=SAMPLE_VALIDATION_RESULT
    )
    await db_session.commit()
    answered_id, workspace_id = q.id, workspace.id

    await _authenticate(normal_user)
    response = await client.post(
        "/api/v1/user-feedback",
        json={
            "workspace_id": workspace_id,
            "answers": [{"questionnaire_id": answered_id, "user_answers": ["\U0001F600", "\U0001F610"]}],
        },
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert f"Question {answered_id} only accepts a single choice answer." in response.json()["detail"]


@pytest.mark.asyncio
async def test_submit_feedback_emoji_invalid_option_rejected(
    client: AsyncClient, normal_user: User, db_session: AsyncSession
):
    q = await seed_feedback_question(
        db_session,
        question="Rate us",
        answer_type="emoji",
        optional=False,
        answers=["\U0001F641", "\U0001F610", "\U0001F600"],
    )
    workspace = await create_workspace(
        db_session, user_id=normal_user.id, name="Idea", validation_result=SAMPLE_VALIDATION_RESULT
    )
    await db_session.commit()
    answered_id, workspace_id = q.id, workspace.id

    await _authenticate(normal_user)
    response = await client.post(
        "/api/v1/user-feedback",
        json={
            "workspace_id": workspace_id,
            "answers": [{"questionnaire_id": answered_id, "user_answers": ["\U0001F92F"]}],
        },
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "is not a valid option for question" in response.json()["detail"]


@pytest.mark.asyncio
async def test_submit_feedback_emoji_valid_success(
    client: AsyncClient, normal_user: User, db_session: AsyncSession
):
    q = await seed_feedback_question(
        db_session,
        question="Rate us",
        answer_type="emoji",
        optional=False,
        answers=["\U0001F641", "\U0001F610", "\U0001F600"],
    )
    workspace = await create_workspace(
        db_session, user_id=normal_user.id, name="Idea", validation_result=SAMPLE_VALIDATION_RESULT
    )
    await db_session.commit()
    answered_id, workspace_id = q.id, workspace.id

    await _authenticate(normal_user)
    response = await client.post(
        "/api/v1/user-feedback",
        json={
            "workspace_id": workspace_id,
            "answers": [{"questionnaire_id": answered_id, "user_answers": ["\U0001F600"]}],
        },
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.headers["content-type"] == "application/pdf"


@pytest.mark.asyncio
async def test_submit_feedback_success_returns_pdf_and_triggers_certificate(
    client: AsyncClient, normal_user: User, db_session: AsyncSession
):
    q1 = await seed_feedback_question(db_session, question="How was it?", answer_type="textarea", optional=False)
    q2 = await seed_feedback_question(db_session, question="Choose", answer_type="checkboxes", optional=True, answers=["A", "B"])
    workspace = await create_workspace(
        db_session, user_id=normal_user.id, name="My Startup", validation_result=SAMPLE_VALIDATION_RESULT
    )
    await db_session.commit()

    await _authenticate(normal_user)
    response = await client.post(
        "/api/v1/user-feedback",
        json={
            "workspace_id": workspace.id,
            "answers": [
                {"questionnaire_id": q1.id, "user_answers": ["Great!"]},
                {"questionnaire_id": q2.id, "user_answers": ["A", "B"]},
            ],
        },
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.headers["content-type"] == "application/pdf"
    assert response.content[:5] == b"%PDF-"
    assert "attachment" in response.headers.get("content-disposition", "")

    res = await db_session.execute(
        select(UserFeedbackQuestionnaire).where(UserFeedbackQuestionnaire.user_id == normal_user.id)
    )
    records = res.scalars().all()
    assert len(records) == 2
    record_map = {r.questionnaire_id: r for r in records}
    assert record_map[q1.id].user_answers == ["Great!"]
    assert record_map[q2.id].user_answers == ["A", "B"]
    assert record_map[q1.id].workspace_id == workspace.id


@pytest.mark.asyncio
async def test_submit_feedback_one_time_upsert(
    client: AsyncClient, normal_user: User, db_session: AsyncSession
):
    q = await seed_feedback_question(db_session, question="Feedback", answer_type="textarea", optional=False)
    workspace = await create_workspace(
        db_session, user_id=normal_user.id, name="Idea", validation_result=SAMPLE_VALIDATION_RESULT
    )
    await db_session.commit()

    await _authenticate(normal_user)
    payload = {
        "workspace_id": workspace.id,
        "answers": [{"questionnaire_id": q.id, "user_answers": ["First"]}],
    }
    first = await client.post("/api/v1/user-feedback", json=payload)
    assert first.status_code == status.HTTP_200_OK

    payload["answers"] = [{"questionnaire_id": q.id, "user_answers": ["Second"]}]
    second = await client.post("/api/v1/user-feedback", json=payload)
    assert second.status_code == status.HTTP_200_OK

    res = await db_session.execute(
        select(UserFeedbackQuestionnaire).where(UserFeedbackQuestionnaire.user_id == normal_user.id)
    )
    records = res.scalars().all()
    assert len(records) == 1
    assert records[0].user_answers == ["Second"]


@pytest.mark.asyncio
async def test_submit_feedback_rejects_unvalidated_workspace(
    client: AsyncClient, normal_user: User, db_session: AsyncSession
):
    q = await seed_feedback_question(db_session, question="Feedback", answer_type="textarea", optional=False)
    workspace = await create_workspace(db_session, user_id=normal_user.id, name="Not Validated")
    await db_session.commit()

    await _authenticate(normal_user)
    response = await client.post(
        "/api/v1/user-feedback",
        json={
            "workspace_id": workspace.id,
            "answers": [{"questionnaire_id": q.id, "user_answers": ["x"]}],
        },
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "has not been validated yet" in response.json()["detail"]


@pytest.mark.asyncio
async def test_submit_feedback_rejects_foreign_workspace(
    client: AsyncClient, normal_user: User, db_session: AsyncSession
):
    other = User(username="other@axiorapulse.com", register_mfa=True)
    db_session.add(other)
    await db_session.flush()
    workspace = await create_workspace(db_session, user_id=other.id, name="Someone else's")
    q = await seed_feedback_question(db_session, question="Feedback", answer_type="textarea", optional=False)
    await db_session.commit()

    await _authenticate(normal_user)
    response = await client.post(
        "/api/v1/user-feedback",
        json={
            "workspace_id": workspace.id,
            "answers": [{"questionnaire_id": q.id, "user_answers": ["x"]}],
        },
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


# ──────────────────────────────────────────────────────────────────────────────
# Admin List User Feedback (with filters)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_user_feedback_requires_auth(client: AsyncClient):
    response = await client.get("/api/v1/user-feedback")
    assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)


@pytest.mark.asyncio
async def test_list_user_feedback_requires_admin(client: AsyncClient, normal_user: User):
    await _authenticate(normal_user)
    response = await client.get("/api/v1/user-feedback")
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_list_user_feedback_with_filters(
    client: AsyncClient, admin_user: User, db_session: AsyncSession
):
    q1 = await seed_feedback_question(db_session, question="Rating", answer_type="textarea", optional=False)
    q2 = await seed_feedback_question(db_session, question="Suggestion", answer_type="textarea", optional=True)
    workspace = await create_workspace(
        db_session, user_id=admin_user.id, name="WS", validation_result=SAMPLE_VALIDATION_RESULT
    )
    user_b = User(username="beta@axiorapulse.com", register_mfa=True)
    db_session.add(user_b)
    await db_session.flush()

    db_session.add_all([
        UserFeedbackQuestionnaire(
            user_id=admin_user.id, workspace_id=workspace.id, questionnaire_id=q1.id,
            user_answers=["Amazing product"], question_snapshot=q1.question,
            submission_date=datetime(2026, 1, 10),
        ),
        UserFeedbackQuestionnaire(
            user_id=admin_user.id, workspace_id=workspace.id, questionnaire_id=q2.id,
            user_answers=["Add dark mode"], question_snapshot=q2.question,
            submission_date=datetime(2026, 1, 15),
        ),
        UserFeedbackQuestionnaire(
            user_id=user_b.id, workspace_id=workspace.id, questionnaire_id=q1.id,
            user_answers=["Needs improvement"], question_snapshot=q1.question,
            submission_date=datetime(2026, 2, 20),
        ),
    ])
    await db_session.commit()

    await _authenticate(admin_user)

    # All — total counts the number of USERS who submitted (2), while the listing
    # still returns one row per answered question (3).
    response = await client.get("/api/v1/user-feedback")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["pagination"]["total"] == 2
    assert len(data["feedback"]) == 3

    # Filter by user_id
    response = await client.get(f"/api/v1/user-feedback?user_id={user_b.id}")
    data = response.json()
    assert data["pagination"]["total"] == 1
    assert data["feedback"][0]["user_email"] == "beta@axiorapulse.com"

    # Search
    response = await client.get("/api/v1/user-feedback?search=dark mode")
    data = response.json()
    assert data["pagination"]["total"] == 1
    assert data["feedback"][0]["user_answers"] == ["Add dark mode"]

    response = await client.get("/api/v1/user-feedback?search=Suggestion")
    data = response.json()
    assert data["pagination"]["total"] == 1
    assert data["feedback"][0]["question"] == "Suggestion"

    # Date range — both January rows are from the SAME user, so total is 1 user.
    response = await client.get("/api/v1/user-feedback?date_from=2026-01-01T00:00:00&date_to=2026-01-31T00:00:00")
    data = response.json()
    assert data["pagination"]["total"] == 1
    assert len(data["feedback"]) == 2

    # Pagination — total is the distinct-user count, unaffected by limit/offset.
    response = await client.get("/api/v1/user-feedback?limit=2&offset=0")
    data = response.json()
    assert len(data["feedback"]) == 2
    assert data["pagination"]["total"] == 2
    assert data["pagination"]["limit"] == 2
    assert data["pagination"]["offset"] == 0


# ──────────────────────────────────────────────────────────────────────────────
# Service-level coverage
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_edit_keeps_historical_question_snapshot(
    client: AsyncClient, admin_user: User, db_session: AsyncSession
):
    q = await seed_feedback_question(
        db_session, question="Original question", answer_type="textarea", optional=False
    )
    workspace = await create_workspace(
        db_session, user_id=admin_user.id, name="WS", validation_result=SAMPLE_VALIDATION_RESULT
    )
    await db_session.commit()

    await _authenticate(admin_user)

    response = await client.post(
        "/api/v1/user-feedback",
        json={
            "workspace_id": workspace.id,
            "answers": [{"questionnaire_id": q.id, "user_answers": ["ok"]}],
        },
    )
    assert response.status_code == status.HTTP_200_OK

    # Admin edits the template question text.
    response = await client.put(
        f"/api/v1/admin/feedback-questionnaire/{q.id}",
        json={"question": "Edited question"},
    )
    assert response.status_code == status.HTTP_200_OK

    # The historical submission must still show the question the user actually saw.
    response = await client.get("/api/v1/user-feedback")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["feedback"][0]["question"] == "Original question"

    stored = (
        await db_session.execute(
            select(UserFeedbackQuestionnaire).where(
                UserFeedbackQuestionnaire.questionnaire_id == q.id
            )
        )
    ).scalar_one()
    assert stored.question_snapshot == "Original question"


@pytest.mark.asyncio
async def test_service_create_feedback_question_non_admin(normal_user: User, db_session: AsyncSession):
    from app.services.feedback_questionnaire_service import feedback_questionnaire_service

    payload = CreateFeedbackQuestionRequest(
        question="Q", answer_type="textarea", optional=True, answers=[], is_display=True
    )
    with pytest.raises(Exception) as exc:
        await feedback_questionnaire_service.create_question(payload, current_user=normal_user, db=db_session)
    assert "Admin privileges required." in str(exc.value.detail)


@pytest.mark.asyncio
async def test_service_certificate_failure_propagates(normal_user: User, db_session: AsyncSession):
    from app.services.feedback_questionnaire_service import feedback_questionnaire_service

    q = await seed_feedback_question(db_session, question="Q", answer_type="textarea", optional=False)
    await db_session.commit()
    payload = UserFeedbackSubmitRequest(
        workspace_id=999,
        answers=[{"questionnaire_id": q.id, "user_answers": ["x"]}],
    )
    with pytest.raises(Exception):
        await feedback_questionnaire_service.submit_feedback(payload, current_user=normal_user, db=db_session)