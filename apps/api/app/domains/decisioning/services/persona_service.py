import json
from functools import lru_cache
from pathlib import Path
from typing import Any


_PERSONA_ROOT = Path(__file__).resolve().parents[1] / "personas" / "orchid_sales_v1"


@lru_cache(maxsize=1)
def _load_persona_assets() -> dict[str, Any]:
    modes = json.loads((_PERSONA_ROOT / "modes.json").read_text(encoding="utf-8"))
    return {
        "soul": (_PERSONA_ROOT / "SOUL.md").read_text(encoding="utf-8").strip(),
        "style": (_PERSONA_ROOT / "STYLE.md").read_text(encoding="utf-8").strip(),
        "policy": (_PERSONA_ROOT / "POLICY.md").read_text(encoding="utf-8").strip(),
        "modes": modes,
    }


def build_static_persona_prompt(*, mode: str = "care_companion") -> str:
    assets = _load_persona_assets()
    instructions = assets["modes"].get(mode, [])
    mode_text = "\n".join(f"- {item}" for item in instructions)
    return (
        f"{assets['policy']}\n\n"
        f"{assets['soul']}\n\n"
        f"{assets['style']}\n\n"
        f"# 当前表达模式：{mode}\n{mode_text}"
    ).strip()
