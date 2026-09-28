from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from main import app
from app.core.dependencies import get_current_user
from app.db.models import SupportTicket, SupportTicketEvent
from app.services.email_service import OTPResult, send_ticket_update_email
from app.workers.ticket_notifications import deliver_pending

pytestmark = pytest.mark.asyncio
BASE = '/api/v1/tickets'


def login(user):
    app.dependency_overrides[get_current_user] = lambda: user


async def create(client, user):
    login(user)
    response = await client.post(BASE, json={
        'category': 'Technical Issue', 'subject': 'Help', 'description': 'Cannot open workspace',
        'user': {'id': 'fake', 'email': 'attacker@example.com'},
    })
    assert response.status_code == 200, response.text
    return response.json()


async def test_admin_actions_are_audited_and_queue_owner_email(client, db_session, normal_user, admin_user):
    ticket = await create(client, normal_user)
    assert ticket['userEmail'] == normal_user.username
    login(admin_user)
    for path, payload in [('assign', {'agentName': 'Sarah'}), ('status', {'status': 'Resolved'}),
                          ('reply', {'message': 'Fixed now', 'senderRole': 'user'})]:
        method = client.post if path == 'reply' else client.patch
        response = await method(f'{BASE}/{ticket["id"]}/{path}', json=payload)
        assert response.status_code == 200, response.text
    events = (await db_session.execute(select(SupportTicketEvent).where(SupportTicketEvent.email_status == 'pending'))).scalars().all()
    assert {e.action for e in events} == {'assigned', 'status_changed', 'admin_reply'}
    assert all(e.recipient == normal_user.username and e.actor_id == admin_user.id for e in events)
    persisted = await db_session.get(SupportTicket, ticket['id'])
    assert persisted.data['status'] == 'Resolved'
    assert persisted.data['assignedTo'] == 'Sarah'
    assert persisted.data['unreadByUser']
    # Selecting the same status does not send duplicate notifications.
    await client.patch(f'{BASE}/{ticket["id"]}/status', json={'status': 'Resolved'})
    assert len((await db_session.execute(select(SupportTicketEvent).where(SupportTicketEvent.email_status == 'pending'))).scalars().all()) == 3


async def test_notes_private_and_actions_authorized(client, db_session, normal_user, admin_user):
    ticket = await create(client, normal_user)
    denied = await client.patch(f'{BASE}/{ticket["id"]}/status', json={'status': 'Closed'})
    assert denied.status_code == 403
    login(admin_user)
    response = await client.post(f'{BASE}/{ticket["id"]}/note', json={'note': 'Private staff note'})
    assert response.status_code == 200
    login(normal_user)
    response = await client.get(BASE)
    assert response.json()[0]['internalNotes'] == []
    events = (await db_session.execute(select(SupportTicketEvent))).scalars().all()
    assert all(e.email_status == 'not_required' for e in events)
    # A user's supplied role must not create an admin reply/email.
    response = await client.post(f'{BASE}/{ticket["id"]}/reply', json={'message': 'Thanks', 'senderRole': 'admin'})
    assert response.json()['messages'][-1]['senderRole'] == 'user'


async def test_other_users_cannot_read_or_reply(client, db_session, normal_user, admin_user):
    ticket = await create(client, admin_user)
    login(normal_user)
    assert (await client.get(BASE)).json() == []
    assert (await client.post(f'{BASE}/{ticket["id"]}/reply', json={'message': 'x'})).status_code == 404
    await db_session.refresh(normal_user)
    assert (await client.patch(f'{BASE}/{ticket["id"]}/read-user')).status_code == 404


async def test_invalid_status_and_unauthenticated_access(client, normal_user, admin_user):
    ticket = await create(client, normal_user)
    login(admin_user)
    assert (await client.patch(f'{BASE}/{ticket["id"]}/status', json={'status': 'bad'})).status_code == 422
    app.dependency_overrides.pop(get_current_user)
    assert (await client.get(BASE)).status_code in (401, 403)


async def test_durable_delivery_success_and_no_resend(client, db_session, test_engine, normal_user, admin_user):
    ticket = await create(client, normal_user)
    login(admin_user)
    await client.patch(f'{BASE}/{ticket["id"]}/status', json={'status': 'Resolved'})
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    sender = AsyncMock(return_value=OTPResult(True, 'email'))
    with patch('app.workers.ticket_notifications.send_ticket_update_email', sender):
        assert await deliver_pending(factory)
        assert not await deliver_pending(factory)
    event = (await db_session.execute(select(SupportTicketEvent).where(SupportTicketEvent.action == 'status_changed'))).scalar_one()
    assert event.email_status == 'sent' and event.sent_at and event.attempts == 1
    assert sender.call_args.kwargs['to_email'] == normal_user.username


async def test_failed_email_retries_and_records_error(client, db_session, test_engine, normal_user, admin_user):
    ticket = await create(client, normal_user)
    login(admin_user)
    await client.patch(f'{BASE}/{ticket["id"]}/assign', json={'agentName': 'Sarah'})
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    with patch('app.workers.ticket_notifications.send_ticket_update_email', AsyncMock(return_value=OTPResult(False, 'email', 'SMTP unavailable'))):
        for attempt in range(1, 4):
            assert await deliver_pending(factory)
            event = (await db_session.execute(select(SupportTicketEvent).where(SupportTicketEvent.action == 'assigned'))).scalar_one()
            await db_session.refresh(event)
            assert event.attempts == attempt
            if attempt < 3:
                assert event.email_status == 'pending'
                assert not await deliver_pending(factory)
                event.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                await db_session.commit()
        assert event.email_status == 'failed' and event.last_error == 'SMTP unavailable'
        assert not await deliver_pending(factory)
    persisted = await db_session.get(SupportTicket, ticket['id'])
    assert persisted.data['assignedTo'] == 'Sarah'


async def test_email_escapes_user_content():
    with patch('app.services.email_service._smtp_send') as smtp:
        result = await send_ticket_update_email('user@example.com', 'TCK-1', '<script>x</script>', '<b>User</b>', '<img src=x>', 'event-1')
    assert result.success
    msg = smtp.call_args.args[1]
    html = msg.get_payload()[1].get_payload(decode=True).decode()
    assert '<script>x</script>' not in html and '&lt;script&gt;' in html
    assert msg['Message-ID'] == '<ticket-event-1@axiorapulse.com>'


async def test_failed_transaction_does_not_queue_email(client, db_session, normal_user, admin_user):
    ticket = await create(client, normal_user)
    login(admin_user)
    with patch.object(db_session, 'commit', AsyncMock(side_effect=RuntimeError('database unavailable'))):
        with pytest.raises(RuntimeError, match='database unavailable'):
            await client.patch(f'{BASE}/{ticket["id"]}/status', json={'status': 'Resolved'})
    persisted = await db_session.get(SupportTicket, ticket['id'])
    assert persisted.data['status'] == 'Open'
    events = (await db_session.execute(select(SupportTicketEvent).where(SupportTicketEvent.email_status == 'pending'))).scalars().all()
    assert events == []
