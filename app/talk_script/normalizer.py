import re
import unicodedata


_PUNCTUATION_MAP = str.maketrans(
    {
        "，": ",",
        "。": ".",
        "？": "?",
        "！": "!",
        "：": ":",
        "；": ";",
        "（": "(",
        "）": ")",
        "【": "[",
        "】": "]",
        "、": " ",
    }
)


def normalize_message(message: str) -> str:
    text = unicodedata.normalize("NFKC", message or "")
    text = text.translate(_PUNCTUATION_MAP)
    text = re.sub(r"\s+", " ", text)
    return text.strip()
