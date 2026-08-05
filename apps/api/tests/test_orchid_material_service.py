from pathlib import Path

from app.domains.catalog.services.orchid_material_service import (
    ORCHID_MATERIAL_ASSET,
    ORCHID_MATERIAL_CARD,
    ORCHID_MATERIAL_REF,
)


def test_orchid_material_registry_exposes_verified_asset_facts():
    assert ORCHID_MATERIAL_REF == "material:orchid-companion"
    assert ORCHID_MATERIAL_ASSET["material_ref"] == ORCHID_MATERIAL_REF
    assert ORCHID_MATERIAL_ASSET["card"] is ORCHID_MATERIAL_CARD
    assert ORCHID_MATERIAL_CARD["title"] == "萧岚苑陪伴养兰资料"
    assert ORCHID_MATERIAL_CARD["url"].startswith("https://h5.youzan.com/")
    assert "核实购买权益" in ORCHID_MATERIAL_ASSET["access"]


def test_orchid_material_card_thumbnail_is_delivery_ready():
    assert ORCHID_MATERIAL_CARD["thumb_url"] == (
        "http://150.158.52.233/static/orchid-material/"
        "companion-material-card-thumb.jpg"
    )
    thumb_path = (
        Path(__file__).parents[1]
        / "app/static/orchid-material/companion-material-card-thumb.jpg"
    )
    assert thumb_path.stat().st_size < 51_200
