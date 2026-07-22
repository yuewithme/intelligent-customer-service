import re


SHORT_REPLY_CHARS = 100
SENTENCES_PER_MESSAGE = 2
EMERGENCY_SENTENCE_CHARS = 200
_WEAK_MESSAGE_ENDINGS = "，,、；;：:"
_MARKDOWN_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MARKDOWN_PREFIX_RE = re.compile(
    r"^\s*(?:#{1,6}\s+|>\s*|[-+*]\s+|\d+[.)、]\s*)"
)
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[。！？!?])")
_CLAUSE_BOUNDARY_RE = re.compile(r"(?<=[，、；;：:])")
_SPECIAL_SYMBOLS = str.maketrans("", "", "“”‘’\"'「」『』【】[]（）()—–")


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
        line = line.translate(_SPECIAL_SYMBOLS)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def split_customer_messages(text: str) -> list[str]:
    """Keep ordinary replies intact; split long replies every two sentences."""
    semantic_messages = _semantic_messages(text)
    if len(semantic_messages) > 1:
        return coalesce_customer_messages(semantic_messages)

    plain = plain_customer_text(text)
    normalized = " ".join(plain.splitlines()).strip()
    if not normalized:
        return []
    if len(normalized) <= SHORT_REPLY_CHARS:
        return [normalized]

    sentences = [part.strip() for part in _SENTENCE_BOUNDARY_RE.split(normalized) if part.strip()]
    messages: list[str] = []
    for index in range(0, len(sentences), SENTENCES_PER_MESSAGE):
        group = sentences[index : index + SENTENCES_PER_MESSAGE]
        if all(len(sentence) <= EMERGENCY_SENTENCE_CHARS for sentence in group):
            messages.append("".join(group))
            continue
        for sentence in group:
            messages.extend(_split_exceptionally_long_sentence(sentence))
    return coalesce_customer_messages(messages)


def coalesce_customer_messages(messages: list[str]) -> list[str]:
    """Merge short replies and fragments that end at a weak punctuation mark."""
    cleaned = [str(message).strip() for message in messages if str(message).strip()]
    if not cleaned:
        return []

    combined = "".join(cleaned)
    if len(combined) <= SHORT_REPLY_CHARS:
        return [combined]

    merged: list[str] = []
    pending = ""
    for message in cleaned:
        current = f"{pending}{message}"
        pending = ""
        if len(current) <= SHORT_REPLY_CHARS and current.endswith(tuple(_WEAK_MESSAGE_ENDINGS)):
            pending = current
        else:
            merged.append(current)
    if pending:
        merged.append(pending)
    return merged


def _semantic_messages(text: str) -> list[str]:
    paragraphs = re.split(r"\r?\n\s*\r?\n+", str(text or ""))
    messages: list[str] = []
    for paragraph in paragraphs:
        message = plain_customer_text(paragraph).replace("\n", " ").strip()
        if message:
            messages.append(message)
    return messages


def _split_exceptionally_long_sentence(sentence: str) -> list[str]:
    if len(sentence) <= EMERGENCY_SENTENCE_CHARS:
        return [sentence]

    clauses = [part.strip() for part in _CLAUSE_BOUNDARY_RE.split(sentence) if part.strip()]
    if len(clauses) <= 1:
        return [sentence]

    messages: list[str] = []
    current = ""
    for clause in clauses:
        if current and len(current) + len(clause) > EMERGENCY_SENTENCE_CHARS:
            messages.append(current)
            current = clause
        else:
            current += clause
    if current:
        messages.append(current)
    return messages
