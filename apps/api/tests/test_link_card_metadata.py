import json
from urllib.parse import quote

import pytest

from app.domains.sales.schemas.unpurchased_sop import (
    UnpurchasedSopMessageRequest,
    UnpurchasedSopStepRequest,
)
from app.services import link_card_metadata_service
from app.integrations.web.services.link_card_metadata_service import (
    LinkCardMetadata,
    _parse_link_card_metadata,
    enrich_sop_link_cards,
)


def test_parse_youzan_link_card_metadata():
    goods_data = {
        "column": {
            "title": "兰花课堂",
            "picture": {"cover": "https://img01.yzcdn.cn/column.jpg"},
        },
        "content": {
            "title": "兰花标准上盆示范",
            "summary": "标准上盆步骤与注意事项",
            "columnTitle": "兰花课堂",
            "cover": "https://img01.yzcdn.cn/course.jpg",
        },
    }
    encoded = quote(json.dumps(goods_data, ensure_ascii=False), safe="")
    html = f'<script>window._global = {{"goodsData":"{encoded}"}}</script>'

    metadata = _parse_link_card_metadata(html, "https://j.youzan.com/yddHbe")

    assert metadata.title == "兰花标准上盆示范"
    assert metadata.description == "标准上盆步骤与注意事项"
    assert metadata.thumb_url == (
        "https://img01.yzcdn.cn/course.jpg"
        "?imageView2/2/w/300/h/300/q/60/format/jpg"
    )


def test_parse_generic_open_graph_metadata():
    html = """
    <html><head>
      <meta property="og:title" content="养兰指南">
      <meta property="og:description" content="新手养兰完整教程">
      <meta property="og:image" content="/images/card.jpg">
    </head></html>
    """

    metadata = _parse_link_card_metadata(html, "https://example.com/course/1")

    assert metadata == LinkCardMetadata(
        title="养兰指南",
        description="新手养兰完整教程",
        thumb_url="https://example.com/images/card.jpg",
    )


@pytest.mark.asyncio
async def test_enrich_url_only_sop_link_card(monkeypatch):
    async def fake_fetch(url: str) -> LinkCardMetadata:
        assert url == "https://j.youzan.com/yddHbe"
        return LinkCardMetadata(
            title="兰花标准上盆示范",
            description="兰花课堂",
            thumb_url="https://img01.yzcdn.cn/course.jpg",
        )

    monkeypatch.setattr(link_card_metadata_service, "fetch_link_card_metadata", fake_fetch)
    request = UnpurchasedSopStepRequest(
        day_offset=0,
        send_time_start="09:00",
        send_time_end="11:00",
        messages=[
            UnpurchasedSopMessageRequest(
                message_type="link_card",
                content="https://j.youzan.com/yddHbe",
                url="https://j.youzan.com/yddHbe",
            )
        ],
        position=0,
        enabled=True,
    )

    await enrich_sop_link_cards(request)

    card = request.messages[0]
    assert card.title == "兰花标准上盆示范"
    assert card.description == "兰花课堂"
    assert card.thumb_url == "https://img01.yzcdn.cn/course.jpg"
