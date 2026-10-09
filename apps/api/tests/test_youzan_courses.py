import asyncio
import json
import sqlite3
from types import SimpleNamespace
from urllib.parse import quote

import httpx
import pytest

from app.core.config import get_settings
from app.domains.decisioning.services import agent_tools
from app.domains.sales.services import care_manual_service as manuals
from app.integrations.youzan.services import youzan_course_service as courses


ALIAS = "3npxpb3qwnd5ixs"
URL = f"https://h5.youzan.com/wscvis/course/detail/{ALIAS}?kdt_id=180281510"


def page(**overrides):
    record = dict(id=449557876, alias=ALIAS, kdtId=180281510, title="兰花课堂",
                  status=1, price=0, contentsCount=207, cover="https://img.example.com/course.jpg",
                  publishAt=1791500000000, access=0, isLock=False)
    record.update(overrides)
    raw = json.dumps(record, ensure_ascii=False)
    escaped = "".join(f"%u{ord(c):04X}" if ord(c) > 255 else quote(c, safe="") for c in raw)
    return "<script>window._global = " + json.dumps({"goodsData": escaped}) + ";</script>"


def parsed(**overrides):
    return courses.parse_course_page(page(**overrides), alias=ALIAS, kdt_id=180281510)


@pytest.fixture
def store(monkeypatch, tmp_path):
    path = tmp_path / "courses.db"
    monkeypatch.setattr(get_settings(), "database_url", f"sqlite:///{path.as_posix()}")
    monkeypatch.setattr(get_settings(), "unified_library_enabled", False)
    manuals.reset_care_manual_store_for_tests()
    yield path
    manuals.reset_care_manual_store_for_tests()


@pytest.mark.parametrize("price,expected", [(0, 0), (9900, 9900), (None, None), (False, None), ("0", None), (-1, None)])
def test_price_and_encoded_title(price, expected):
    result = parsed(price=price)
    assert result["price_cent"] == expected
    assert result["title"] == "兰花课堂"
    assert result["content_count"] == 207


@pytest.mark.parametrize("overrides", [{"status": 2}, {"status": 0}, {"status": 3}, {"isLock": True}, {"access": 1}])
def test_unavailable_course_status(overrides):
    assert parsed(**overrides)["note_status"] == "unavailable"


@pytest.mark.parametrize("url", [URL.replace("https:", "http:"), URL.replace("h5.youzan.com", "localhost"),
                                URL.replace("h5.youzan.com", "youzan.com.evil.com"),
                                URL.replace("h5.youzan.com", "user@h5.youzan.com"), URL.split("?")[0]])
def test_invalid_sources_rejected(url):
    with pytest.raises(ValueError):
        courses.course_source(url)


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["shop180473678.youzan.com", "localhost"])
async def test_shop_redirect_is_validated(host):
    visited = []

    def respond(request):
        visited.append(request.url.host)
        if len(visited) == 1:
            return httpx.Response(301, headers={"location": URL.replace("h5.youzan.com", host)})
        return httpx.Response(200, text=page())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        if host == "localhost":
            with pytest.raises(ValueError):
                await courses.fetch_courses([URL], client=client)
            assert visited == ["h5.youzan.com"]
        else:
            assert (await courses.fetch_courses([URL], client=client))[0]["price_cent"] == 0
            assert visited == ["h5.youzan.com", host]


def test_sync_keeps_manuals_and_configuration_and_only_matches_free(store):
    manuals._persist_notes([dict(note_id=11, title="【建兰】养护注意事项", note_url="https://example.com/manual", note_status="published")])
    manuals._persist_courses([parsed(), parsed(id=2, title="付费兰花课堂", price=9900), parsed(id=3, title="未知兰花课堂", price=None)])
    free = next(row for row in manuals.list_care_manuals(source_type="course")["items"] if row["price_cent"] == 0)
    manuals.update_care_manual(free["id"], dict(aliases=["养兰视频"], match_keywords=["视频"], sort_order=5, card_description="人工说明", enabled=True))
    manuals._persist_courses([parsed(contentsCount=208)])
    updated = manuals.get_care_manual(free["id"])
    assert (updated["aliases"], updated["sort_order"], updated["card_description"], updated["content_count"]) == (["养兰视频"], 5, "人工说明", 208)
    assert manuals.test_match_care_manuals(query="兰花课堂")["matches"][0]["card_id"] == free["id"]
    assert len(manuals.test_match_care_manuals(query="兰花课堂")["matches"]) == 1
    manuals._persist_notes([])
    assert manuals.get_care_manual(free["id"])["available"]
    manuals._persist_courses([parsed(status=2)])
    assert not manuals.get_care_manual(free["id"])["auto_send_allowed"]
    assert manuals.test_match_care_manuals(query="兰花课堂")["matches"] == []


@pytest.mark.asyncio
async def test_import_is_idempotent_and_partial_failure_preserves_data(store):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=page()))) as client:
        result = await manuals.sync_care_manuals(source_type="course", course_urls=[URL, URL], client=client)
        assert result["created_count"] == 1
        result = await manuals.sync_care_manuals(source_type="course", client=client)
        assert result["updated_count"] == 1 and result["created_count"] == 0
    before = manuals.list_care_manuals(source_type="course")["items"]

    def respond(request):
        if ALIAS in request.url.path:
            return httpx.Response(200, text=page(price=100))
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await manuals.sync_care_manuals(source_type="course", course_urls=[URL.replace(ALIAS, "abcdefghi")], client=client)
    after = manuals.list_care_manuals(source_type="course")
    assert after["items"] == before
    assert after["last_sync"]["status"] == "failed"
    assert manuals.list_care_manuals(source_type="manual")["last_sync"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("price,status,expected", [(0, 1, "prepared"), (9900, 1, "forbidden"), (None, 1, "forbidden"), (0, 2, "forbidden")])
async def test_agent_rechecks_current_price_and_state(monkeypatch, price, status, expected):
    record = dict(id=1, source_type="course", available=True, price_cent=0, youzan_note_id="course:449557876", note_url=URL)
    monkeypatch.setattr(agent_tools, "get_care_manual", lambda _: record)

    async def fetch(urls):
        assert urls == [URL]
        return [parsed(price=price, status=status)]

    async def recent(*args):
        return False

    monkeypatch.setattr(courses, "fetch_courses", fetch)
    monkeypatch.setattr(agent_tools, "_material_recently_sent", recent)
    context = SimpleNamespace(prepared={})
    result = await agent_tools._material_send(call_id="send", arguments={"material_ref": "material:care-manual:1"}, context=context)
    assert result.status == expected
    assert bool(context.prepared) == (expected == "prepared")
    if expected == "prepared":
        assert json.loads(context.prepared["send"][0].content)["url"] == URL


@pytest.mark.asyncio
@pytest.mark.parametrize("price", [9900, None])
async def test_paid_or_unknown_local_course_never_fetches_or_prepares(monkeypatch, price):
    monkeypatch.setattr(agent_tools, "get_care_manual", lambda _: dict(source_type="course", available=True, price_cent=price))
    monkeypatch.setattr(courses, "fetch_courses", lambda _: pytest.fail("paid course fetched"))
    context = SimpleNamespace(prepared={})
    result = await agent_tools._material_send(call_id="send", arguments={"material_ref": "material:care-manual:1"}, context=context)
    assert result.status == "forbidden" and context.prepared == {}


@pytest.mark.asyncio
async def test_agent_rejects_unverifiable_course(monkeypatch):
    monkeypatch.setattr(agent_tools, "get_care_manual", lambda _: dict(source_type="course", available=True, price_cent=0, note_url=URL))

    async def offline(*args):
        raise httpx.ConnectError("unavailable")

    monkeypatch.setattr(courses, "fetch_courses", offline)
    context = SimpleNamespace(prepared={})
    result = await agent_tools._material_send(call_id="send", arguments={"material_ref": "material:care-manual:1"}, context=context)
    assert result.status == "forbidden" and not context.prepared


@pytest.mark.asyncio
async def test_course_scheduler_runs_without_openapi_credentials(monkeypatch):
    monkeypatch.setattr(get_settings(), "youzan_enabled", False)
    monkeypatch.setattr(get_settings(), "youzan_care_manual_sync_enabled", True)
    monkeypatch.setattr(manuals, "youzan_credentials_available", lambda: False)
    stop = asyncio.Event()
    calls = []

    async def wait(awaitable, *, timeout):
        awaitable.close()
        raise asyncio.TimeoutError

    async def sync(**kwargs):
        calls.append(kwargs)
        stop.set()

    monkeypatch.setattr(asyncio, "wait_for", wait)
    monkeypatch.setattr(manuals, "sync_care_manuals", sync)
    await manuals.care_manual_sync_worker(stop, source_type="course")
    assert calls == [{"trigger": "scheduled", "source_type": "course"}]


def test_existing_database_migration_preserves_manuals(store):
    manuals._persist_notes([dict(note_id=11, title="【建兰】养护注意事项", note_url="https://example.com/manual", note_status="published")])
    with sqlite3.connect(store) as connection:
        for column in ("source_type", "price_cent", "content_count"):
            connection.execute(f"ALTER TABLE care_manual_cards DROP COLUMN {column}")
        connection.execute("ALTER TABLE care_manual_sync_runs DROP COLUMN source_type")
    manuals.reset_care_manual_store_for_tests()
    row = manuals.list_care_manuals()["items"][0]
    assert row["source_type"] == "manual" and row["available"] and row["price_cent"] is None
    manuals.reset_care_manual_store_for_tests()
    assert manuals.list_care_manuals()["total"] == 1
