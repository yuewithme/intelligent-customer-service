import uuid


PREFIXES = {
    "user": "user",
    "session": "sess",
    "knowledge": "kb",
    "document": "doc",
    "chunk": "chunk",
    "tenant": "tenant",
    "message": "msg",
    "request": "req",
    "handoff": "handoff",
    "sop_media": "sopmedia",
    "workbench_media": "workbenchmedia",
}


def generate_id(kind: str) -> str:
    try:
        prefix = PREFIXES[kind]
    except KeyError as exc:
        raise ValueError(f"Unsupported ID kind: {kind}") from exc
    return f"{prefix}_{uuid.uuid4().hex}"

