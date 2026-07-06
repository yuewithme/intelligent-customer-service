import pytest

from app.services.conversation_event_service import ConversationEventBroker


@pytest.mark.asyncio
async def test_broker_delivers_events_to_active_subscribers():
    broker = ConversationEventBroker(queue_size=2)
    queue = broker.subscribe()

    broker.publish({"conversation_id": "wechat:user-1:default", "reason": "message"})

    assert await queue.get() == {
        "conversation_id": "wechat:user-1:default",
        "reason": "message",
    }
    broker.unsubscribe(queue)
    assert broker.subscriber_count == 0


@pytest.mark.asyncio
async def test_broker_keeps_latest_event_for_slow_subscriber():
    broker = ConversationEventBroker(queue_size=1)
    queue = broker.subscribe()

    broker.publish({"conversation_id": "first"})
    broker.publish({"conversation_id": "latest"})

    assert await queue.get() == {"conversation_id": "latest"}
