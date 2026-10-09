"""Replace the production model credential supplied on stdin without logging it."""

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
    lines = [line for line in original.splitlines()
             if not re.match(r"^\s*(?:export\s+)?DASHSCOPE_API_KEY\s*=", line)]
    lines.append(f"DASHSCOPE_API_KEY={value}")
    replacement = "\n".join(lines) + "\n"
    if replacement == original:
        return
    fd, temporary = tempfile.mkstemp(prefix=".backend.env-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(replacement)
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
        "import hashlib,os; print(hashlib.sha256(os.environ.get('DASHSCOPE_API_KEY','').encode()).hexdigest())",
    ], text=True).strip()
    if actual != hashlib.sha256(value.encode()).hexdigest():
        raise RuntimeError("API model credential does not match deployment input")


if __name__ == "__main__":
    credential = sys.stdin.read().strip()
    if not re.fullmatch(r"sk-[a-zA-Z0-9_.-]{16,200}", credential):
        raise SystemExit("Invalid model credential format")
    if sys.argv[1:] == ["--verify"]:
        verify(credential)
        print("API model credential verified")
    elif not sys.argv[1:]:
        update(Path("/etc/intelligent-customer-service/backend.env"), credential)
        print("Production model credential updated")
    else:
        raise SystemExit("Unsupported argument")
