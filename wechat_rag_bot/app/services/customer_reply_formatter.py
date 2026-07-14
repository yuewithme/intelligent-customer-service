import re


MAX_MESSAGE_CHARS = 36
_MARKDOWN_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MARKDOWN_PREFIX_RE = re.compile(
    r"^\s*(?:#{1,6}\s+|>\s*|[-+*]\s+|\d+[.)、]\s*)"
)
_MESSAGE_BOUNDARY_RE = re.compile(r"(?<=[。！？!?；;，,])")


def plain_customer_text(text: str) -> str:
    lines: list[str] = []
    for raw_line in str(text or "").replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if line.startswith(("```", "~~~")):
            continue
        if not line:
            continue
        line = _MARKDOWN_PREFIX_RE.sub("", line)
        if re.fullmatch(r"(?:-{3,}|_{3,}|\*{3,})", line):
            continue
        if line.startswith("|") and line.endswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                continue
            line = " ".join(cell for cell in cells if cell)
        line = _MARKDOWN_LINK_RE.sub(r"\1", line)
        line = re.sub(r"(\*\*|__|~~|`)", "", line)
        line = re.sub(r"(?<!\w)[*_](?!\s)|(?<!\s)[*_](?!\w)", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def split_customer_messages(text: str) -> list[str]:
    plain = plain_customer_text(text)
    messages: list[str] = []
    for line in plain.splitlines():
        for part in _MESSAGE_BOUNDARY_RE.split(line):
            part = part.strip()
            if not part:
                continue
            messages.extend(_split_by_length(part))
    return messages


def _split_by_length(text: str) -> list[str]:
    return [
        text[index : index + MAX_MESSAGE_CHARS]
        for index in range(0, len(text), MAX_MESSAGE_CHARS)
    ]
