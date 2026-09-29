"""Configure the existing production environment without logging credentials."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def values(secret: str) -> dict[str, str]:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{64}", secret):
        raise ValueError("Invalid platform integration credential")
    return {
        "PLATFORM_API_URL": "https://sales-gateway.hzwohu.com/platform-api/v1",
        "PLATFORM_PORTAL_URL": "https://sales-gateway.hzwohu.com/portal",
        "PLATFORM_APP_ORIGIN": "https://sales-agent.hzwohu.com",
        "PLATFORM_SHARED_SECRET": secret,
    }


def update(path: Path, configuration: dict[str, str]) -> None:
    original = path.read_text(encoding="utf-8")
    stat = path.stat()
    pattern = re.compile(r"^\s*(?:export\s+)?(" + "|".join(configuration) + r")\s*=")
    lines = [line for line in original.splitlines() if not pattern.match(line)]
    lines.extend(f"{key}={value}" for key, value in configuration.items())
    replacement = "\n".join(lines) + "\n"
    if original == replacement:
        return
    backup = Path("/srv/intelligent-customer-service/backups") / (
        "backend-before-platform-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".env"
    )
    backup.parent.mkdir(parents=True, exist_ok=True)
    with backup.open("xb") as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(original.encode("utf-8"))
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


def verify(configuration: dict[str, str]) -> None:
    containers = subprocess.check_output([
        "docker", "ps", "--filter", "label=com.docker.compose.project=intelligent-customer-service",
        "--filter", "label=com.docker.compose.service=api", "--format", "{{.ID}}",
    ], text=True).split()
    if len(containers) != 1:
        raise RuntimeError("Expected one running API container")
    environment = json.loads(subprocess.check_output([
        "docker", "inspect", "--format", "{{json .Config.Env}}", containers[0],
    ], text=True))
    actual = dict(item.split("=", 1) for item in environment)
    if any(actual.get(key) != value for key, value in configuration.items()):
        raise RuntimeError("Running platform configuration does not match deployment input")


if __name__ == "__main__":
    configuration = values(sys.stdin.read().strip())
    if sys.argv[1:] == ["--verify"]:
        verify(configuration)
        print("Running platform configuration verified")
    elif not sys.argv[1:]:
        update(Path("/etc/intelligent-customer-service/backend.env"), configuration)
        print("Production platform configuration updated")
    else:
        raise SystemExit("Unsupported argument")
