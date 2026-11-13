"""Receive COS credentials on stdin; never include them in output or argv."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile


ENVIRONMENT = Path("/etc/intelligent-customer-service/backend.env")
BACKUPS = Path("/srv/intelligent-customer-service/backups")


def values(payload: dict) -> dict[str, str]:
    patterns = {
        "COS_SECRET_ID": r"[A-Za-z0-9]{16,128}",
        "COS_SECRET_KEY": r"[A-Za-z0-9+/=_-]{16,128}",
    }
    if not isinstance(payload, dict) or set(payload) != set(patterns):
        raise ValueError("Invalid COS credential input")
    if any(not isinstance(payload[key], str) or not re.fullmatch(pattern, payload[key])
           for key, pattern in patterns.items()):
        raise ValueError("Invalid COS credential input")
    return dict(payload, COS_BUCKET="wohu-lanhua-cdn-1311448032", COS_REGION="ap-shanghai",
                COS_CDN_BASE_URL="https://lanhua-cdn.hzwohu.com")


def update(path: Path, configuration: dict[str, str]) -> None:
    original = path.read_text(encoding="utf-8")
    metadata = path.stat()
    pattern = re.compile(r"^\s*(?:export\s+)?(?:" + "|".join(configuration) + r")\s*=")
    lines = [line for line in original.splitlines() if not pattern.match(line)]
    lines.extend(f"{key}={value}" for key, value in configuration.items())
    replacement = "\n".join(lines) + "\n"
    if original == replacement:
        os.chmod(path, 0o600)
        return
    BACKUPS.mkdir(parents=True, exist_ok=True)
    backup = BACKUPS / ("backend-before-cos-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".env")
    with backup.open("xb") as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(original.encode("utf-8"))
    fd, temporary = tempfile.mkstemp(prefix=".backend.env-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(replacement)
            output.flush()
            os.fsync(output.fileno())
            os.fchown(output.fileno(), metadata.st_uid, metadata.st_gid)
            os.fchmod(output.fileno(), 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def verify(configuration: dict[str, str]) -> None:
    if stat.S_IMODE(ENVIRONMENT.stat().st_mode) != 0o600:
        raise RuntimeError("Production environment permissions are not private")
    containers = subprocess.check_output([
        "docker", "ps", "--filter", "label=com.docker.compose.project=intelligent-customer-service",
        "--filter", "label=com.docker.compose.service=api", "--format", "{{.ID}}",
    ], text=True).split()
    if len(containers) != 1:
        raise RuntimeError("Expected one running API container")
    expected = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
    actual = subprocess.check_output([
        "docker", "exec", containers[0], "python", "-c",
        "import hashlib,json; from app.core.config import get_settings; s=get_settings(); "
        "v={k: getattr(s,k.lower()) for k in " + repr(list(configuration)) + "}; "
        "v={k: x.get_secret_value() if hasattr(x,'get_secret_value') else x for k,x in v.items()}; "
        "print(hashlib.sha256(json.dumps(v,sort_keys=True).encode()).hexdigest())",
    ], text=True).strip()
    if actual != expected:
        raise RuntimeError("Running COS configuration does not match deployment input")


if __name__ == "__main__":
    try:
        configuration = values(json.loads(sys.stdin.read(8193)))
    except (ValueError, TypeError):
        raise SystemExit("Invalid COS credential input") from None
    if sys.argv[1:] == ["--verify"]:
        verify(configuration)
        print("Running COS configuration verified")
    elif not sys.argv[1:]:
        update(ENVIRONMENT, configuration)
        print("Production COS configuration updated")
    else:
        raise SystemExit("Unsupported argument")
