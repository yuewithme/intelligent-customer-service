"""Verify selected production CDN materials without sending WeChat messages."""

import asyncio
import json
import re
import sys
from xml.etree import ElementTree

from app.integrations.eyun.services.eyun_material_service import (
    _load_material,
    ensure_material_sendable,
)


def summary(material):
    root = ElementTree.fromstring(material["raw_xml"])
    node = root.find(".//videomsg" if material["media_type"] == "video" else ".//img")
    fields = ("length", "playlength", "cdnthumblength", "cdnthumbwidth", "cdnthumbheight")
    return {
        "id": material["id"], "type": material["media_type"], "status": material["status"],
        "metadata": {key: node.get(key) for key in fields} if node is not None else {},
    }


async def main():
    if len(sys.argv) != 3 or not re.fullmatch(r"[a-zA-Z0-9-]+", sys.argv[1]):
        raise SystemExit("Expected online wId and comma-separated material IDs")
    if not re.fullmatch(r"[0-9]+(?:,[0-9]+){0,19}", sys.argv[2]):
        raise SystemExit("Expected 1 to 20 material IDs")
    for material_id in dict.fromkeys(map(int, sys.argv[2].split(","))):
        print(json.dumps({"before": summary(_load_material(material_id))}), flush=True)
        material = await ensure_material_sendable(material_id, w_id=sys.argv[1])
        print(json.dumps({"verified": summary(material)}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
