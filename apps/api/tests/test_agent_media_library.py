import hashlib
import json
from datetime import date

import pytest

from app.domains.catalog.services import agent_media_library_service as library
from app.domains.catalog.services.agent_media_copy_service import (
    copy_ref_for,
    media_copy_for,
)
from app.domains.decisioning.services import agent_tools
from app.integrations.eyun.services import message_risk_control_service


def _copy_fields(title: str, category: str) -> dict:
    copy = media_copy_for(title=title, category=category)
    return {
        "copy_ref": copy_ref_for(category=category, topic=copy["copy_topic"]),
        "copy_topic": copy["copy_topic"],
        "copy_type": copy["copy_type"],
        "copy_text": copy["copy_text"],
        "copy_source": copy["copy_source"],
        "copy_version": 1,
        "copy_status": "ready",
    }


def _write_library(tmp_path, monkeypatch):
    root = tmp_path / "agent-material-library"
    image = root / "图文解说类" / "2.兰花病害防治" / "2.兰花病害防治1.jpg"
    video = root / "知识类" / "兰花浇水.mp4"
    thumb = root / ".thumbnails" / "知识类" / "兰花浇水.jpg"
    for path, content in ((image, b"image"), (video, b"video"), (thumb, b"thumb")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    rows = [
        {
            "category": "图文解说类",
            "relative_path": "2.兰花病害防治/2.兰花病害防治1.jpg",
            "bytes": len(b"image"),
            "sha256": hashlib.sha256(b"image").hexdigest(),
        },
        {
            "category": "知识类",
            "relative_path": "兰花浇水.mp4",
            "thumbnail_path": ".thumbnails/知识类/兰花浇水.jpg",
            "bytes": len(b"video"),
            "sha256": hashlib.sha256(b"video").hexdigest(),
        },
    ]
    rows[0].update(_copy_fields("2.兰花病害防治", "图文解说类"))
    rows[1].update(_copy_fields("兰花浇水", "知识类"))
    (root / "manifest.json").write_text(
        json.dumps(rows, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(library.get_settings(), "upload_dir", str(tmp_path))
    monkeypatch.setattr(library.get_settings(), "agent_media_library_base_url", "")
    monkeypatch.setattr(
        library.get_settings(),
        "app_public_base_url",
        "https://service.example.com",
    )
    library.reset_agent_media_library_cache()


def test_remote_library_manifest_builds_public_video_urls(monkeypatch):
    rows = [
        {
            "category": "知识类",
            "relative_path": "兰花浇水.mp4",
            "thumbnail_path": ".thumbnails/知识类/兰花浇水.jpg",
            "bytes": 123,
            "sha256": "a" * 64,
        }
    ]
    rows[0].update(_copy_fields("兰花浇水", "知识类"))

    class Response:
        content = json.dumps(rows, ensure_ascii=False).encode()

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr(library.httpx, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(
        library.get_settings(),
        "agent_media_library_base_url",
        "https://media.example.com/library",
    )
    library.reset_agent_media_library_cache()

    result = library.search_agent_media("浇水视频", category="知识类", limit=1)

    assert result[0]["format"] == "video"
    assert result[0]["url"].startswith("https://media.example.com/library/")
    assert result[0]["thumb_url"].endswith(".jpg")
    assert result[0]["copy_type"] == "养护科普"
    assert "会晾根多久" not in result[0]["copy_text"]


def test_remote_library_rejects_media_without_fixed_copy(monkeypatch):
    rows = [
        {
            "category": "知识类",
            "relative_path": "兰花浇水.mp4",
            "thumbnail_path": ".thumbnails/知识类/兰花浇水.jpg",
            "bytes": 123,
            "sha256": "a" * 64,
        }
    ]

    class Response:
        content = json.dumps(rows, ensure_ascii=False).encode()

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr(library.httpx, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(
        library.get_settings(),
        "agent_media_library_base_url",
        "https://media.example.com/library",
    )
    library.reset_agent_media_library_cache()

    assert library.search_agent_media("浇水视频", limit=1) == []


def test_search_agent_media_returns_stable_public_reference(tmp_path, monkeypatch):
    _write_library(tmp_path, monkeypatch)

    result = library.search_agent_media("兰花病害图文资料", limit=3)

    assert len(result) == 1
    assert result[0]["category"] == "图文解说类"
    assert result[0]["format"] == "image"
    assert result[0]["material_ref"].startswith("material:agent-media:")
    assert "%E5%85%B0%E8%8A%B1" in result[0]["url"]


def test_scheduled_media_selection_is_stable_for_date(tmp_path, monkeypatch):
    _write_library(tmp_path, monkeypatch)

    first = library.select_scheduled_agent_media(
        local_date=date(2026, 8, 5),
        category="知识类",
        copy_type="养护科普",
    )
    second = library.select_scheduled_agent_media(
        local_date=date(2026, 8, 5),
        category="知识类",
        copy_type="养护科普",
    )

    assert first == second
    assert first is not None
    assert first["copy_status"] == "ready"


def test_scheduled_media_randomly_traverses_full_copy_type_without_repeat(
    monkeypatch,
):
    categories = (
        "知识类",
        "图文解说类",
        "AI类",
        "知识类",
        "图文解说类",
    )
    items = tuple(
        {
            "id": f"{index:024d}",
            "category": category,
            "relative_path": f"material-{index}.jpg",
            "title": f"素材 {index}",
            "media_type": "image",
            "bytes": 100,
            "thumbnail_path": "",
            "asset_base_url": "https://media.example.com",
            "copy_ref": f"copy:test:{index}",
            "copy_topic": f"素材 {index}",
            "copy_type": "养护科普",
            "copy_text": f"养护文案 {index}",
            "copy_source": "test",
            "copy_version": 1,
            "copy_status": "ready",
        }
        for index, category in enumerate(categories)
    )
    monkeypatch.setattr(library, "_load_items", lambda: items)
    size = len(items)
    anchor = date(2026, 8, 5).toordinal()
    cycle_start = anchor - anchor % size

    first_cycle = [
        library.select_scheduled_agent_media(
            local_date=date.fromordinal(cycle_start + offset),
            copy_type="养护科普",
        )["material_ref"]
        for offset in range(size)
    ]
    second_cycle = [
        library.select_scheduled_agent_media(
            local_date=date.fromordinal(cycle_start + size + offset),
            copy_type="养护科普",
        )["material_ref"]
        for offset in range(size)
    ]

    expected = {f"material:agent-media:{index:024d}" for index in range(size)}
    assert set(first_cycle) == expected
    assert set(second_cycle) == expected
    assert len(first_cycle) == len(set(first_cycle))
    assert len(second_cycle) == len(set(second_cycle))
    assert first_cycle[-1] != second_cycle[0]
    assert first_cycle != sorted(first_cycle)
    assert second_cycle != first_cycle


@pytest.mark.asyncio
async def test_material_send_prepares_library_video(tmp_path, monkeypatch):
    _write_library(tmp_path, monkeypatch)
    item = library.search_agent_media("浇水知识视频", limit=1)[0]

    async def not_recently_sent(*args, **kwargs):
        return False

    monkeypatch.setattr(agent_tools, "_material_recently_sent", not_recently_sent)
    context = agent_tools.AgentExecutionContext(
        message=type("Message", (), {"channel": "wechat", "user_id": "customer"})(),
        user_state=None,
        workspace={},
    )
    result = await agent_tools.execute_agent_tool(
        call_id="send-media",
        name="material.send",
        arguments={"material_ref": item["material_ref"]},
        context=context,
    )

    assert result.status == "prepared"
    assert [item.type for item in context.prepared["send-media"]] == ["text", "video"]
    assert "浇水" in context.prepared["send-media"][0].content
    payload = json.loads(context.prepared["send-media"][1].content)
    assert payload["path"].endswith(".mp4")
    assert payload["thumb_path"].endswith(".jpg")


@pytest.mark.asyncio
async def test_material_search_category_returns_only_library_items(
    tmp_path, monkeypatch
):
    _write_library(tmp_path, monkeypatch)
    context = agent_tools.AgentExecutionContext(
        message=type("Message", (), {"channel": "wechat", "user_id": "customer"})(),
        user_state=None,
        workspace={},
    )

    result = await agent_tools.execute_agent_tool(
        call_id="search-media",
        name="material.search",
        arguments={"query": "浇水视频", "category": "知识类", "limit": 3},
        context=context,
    )

    assert result.status == "found"
    assert len(result.data["materials"]) == 1
    assert result.data["materials"][0]["format"] == "video"


def test_curated_copy_matches_user_topics(tmp_path, monkeypatch):
    _write_library(tmp_path, monkeypatch)
    root = tmp_path / "agent-material-library"
    video = root / "知识类" / "37.为什么兰花需要晾根.mp4"
    video.write_bytes(b"dry-root-video")
    rows = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    row = {
            "category": "知识类",
            "relative_path": video.name,
            "thumbnail_path": ".thumbnails/知识类/兰花浇水.jpg",
            "bytes": video.stat().st_size,
            "sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
        }
    row.update(_copy_fields("37.为什么兰花需要晾根", "知识类"))
    rows.append(row)
    (root / "manifest.json").write_text(
        json.dumps(rows, ensure_ascii=False), encoding="utf-8"
    )
    library.reset_agent_media_library_cache()

    result = library.search_agent_media(
        "晾根", category="知识类", copy_type="养护科普", limit=1
    )

    assert result[0]["copy_source"] == "curated"
    assert result[0]["copy_text"] == (
        "上盆必须晾根，90% 兰友都忽略！你平时种兰花会晾根多久？"
    )


def test_eyun_outbound_messages_preserve_agent_video():
    messages = message_risk_control_service._outbound_messages(
        {
            "outbound_messages": [
                {
                    "type": "video",
                    "content": json.dumps(
                        {
                            "path": "https://service.example.com/video.mp4",
                            "thumb_path": "https://service.example.com/video.jpg",
                        }
                    ),
                }
            ]
        }
    )

    assert messages[0]["type"] == "video"
    assert json.loads(messages[0]["content"])["thumb_path"].endswith(".jpg")
