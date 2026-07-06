import asyncio
from typing import Any


ConversationEvent = dict[str, Any]


class ConversationEventBroker:
    def __init__(self, queue_size: int = 100):
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue[ConversationEvent]] = set()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self) -> asyncio.Queue[ConversationEvent]:
        queue: asyncio.Queue[ConversationEvent] = asyncio.Queue(
            maxsize=self._queue_size
        )
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[ConversationEvent]) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: ConversationEvent) -> None:
        for queue in tuple(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)


conversation_event_broker = ConversationEventBroker()
