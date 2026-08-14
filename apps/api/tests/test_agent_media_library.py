import hashlib
import json

import pytest

from app.domains.catalog.services import agent_media_library_service as library
from app.domains.decisioning.services import agent_tools
from app.integrations.eyun.services import message_risk_control_service


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


def test_search_agent_media_returns_stable_public_reference(tmp_path, monkeypatch):
    _write_library(tmp_path, monkeypatch)

    result = library.search_agent_media("兰花病害图文资料", limit=3)

    assert len(result) == 1
    assert result[0]["category"] == "图文解说类"
    assert result[0]["format"] == "image"
    assert result[0]["material_ref"].startswith("material:agent-media:")
    assert "%E5%85%B0%E8%8A%B1" in result[0]["url"]


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
    assert context.prepared["send-media"][0].type == "video"
    payload = json.loads(context.prepared["send-media"][0].content)
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
