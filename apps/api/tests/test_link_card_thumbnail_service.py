import io

from PIL import Image

from app.integrations.web.services.link_card_thumbnail_service import (
    EYUN_LINK_CARD_THUMB_MAX_BYTES,
    _compress_thumbnail_bytes,
)


def test_compress_thumbnail_bytes_stays_below_eyun_limit():
    source_image = Image.effect_noise((1200, 1200), 100).convert("RGB")
    source_buffer = io.BytesIO()
    source_image.save(source_buffer, format="JPEG", quality=95)

    compressed = _compress_thumbnail_bytes(source_buffer.getvalue())

    assert len(compressed) < EYUN_LINK_CARD_THUMB_MAX_BYTES
    with Image.open(io.BytesIO(compressed)) as result:
        assert result.format == "JPEG"
        assert max(result.size) <= 640
