"""Update the production notification setting without logging its value."""

import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def update(path: Path, value: str) -> None:
    original = path.read_text(encoding="utf-8")
    stat = path.stat()
    key = "FEISHU_HANDOFF_WEBHOOK_URL"
    lines = [line for line in original.splitlines() if not re.match(rf"^\s*(?:export\s+)?{key}\s*=", line)]
    lines.append(f"{key}={value}")
    fd, temporary = tempfile.mkstemp(prefix=".backend.env-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write("\n".join(lines) + "\n")
            output.flush()
            os.fsync(output.fileno())
            os.fchown(output.fileno(), stat.st_uid, stat.st_gid)
            os.fchmod(output.fileno(), stat.st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def verify(value: str) -> None:
    containers = subprocess.check_output([
        "docker", "ps", "--filter", "label=com.docker.compose.project=intelligent-customer-service",
        "--filter", "label=com.docker.compose.service=api", "--format", "{{.ID}}",
    ], text=True).split()
    if len(containers) != 1:
        raise RuntimeError("Expected one running API container")
    actual = subprocess.check_output([
        "docker", "exec", containers[0], "python", "-c",
        "import hashlib,os; print(hashlib.sha256(os.environ.get('FEISHU_HANDOFF_WEBHOOK_URL','').encode()).hexdigest())",
    ], text=True).strip()
    if actual != hashlib.sha256(value.encode()).hexdigest():
        raise RuntimeError("API webhook configuration does not match deployment input")


if __name__ == "__main__":
    webhook = sys.stdin.read().strip()
    if not re.fullmatch(r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[0-9a-fA-F-]{36}", webhook):
        raise SystemExit("Invalid Feishu webhook format")
    if sys.argv[1:] == ["--verify"]:
        verify(webhook)
        print("API webhook configuration verified")
    elif not sys.argv[1:]:
        update(Path("/etc/intelligent-customer-service/backend.env"), webhook)
        print("Production webhook configuration updated")
    else:
        raise SystemExit("Unsupported argument")
