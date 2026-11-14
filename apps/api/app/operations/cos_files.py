"""Production inventory and explicitly approved public-media uploads."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
import uuid


PART_SIZE = 8 * 1024 * 1024
MAX_BATCH_BYTES = 512 * 1024 * 1024
MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov", ".m4v", ".webm"}
MEDIA_DIRECTORIES = {
    "agent-material-library": "marketing_media",
    "workbench-media": "workbench_media",
    "link-card-thumbs": "link_thumbnails",
}
STATE_DIRECTORY = ".cos-migration"


class SafetyError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise SafetyError(message)


def encoded(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def private_directory(path):
    path.mkdir(mode=0o700, exist_ok=True)
    require(stat.S_ISDIR(path.lstat().st_mode) and not path.is_symlink(), "unsafe state directory")
    path.chmod(0o700)
    return path


def save_json(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_json(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "unsafe state file")
        return json.load(stream)


def verified_public_record(root, item, settings):
    try:
        record = load_json(root / STATE_DIRECTORY / "public-index" / (item["id"] + ".json"))
        require(record["source"] == item, "stale public media index")
        url = record["url"]
        prefix = settings.cos_cdn_base_url.rstrip("/") + "/intelligent-customer-service/assets/"
        require(isinstance(url, str) and url.startswith(prefix) and re.fullmatch(
            r"[a-f0-9]{64}-[a-f0-9]{32}\.[a-z0-9]+", url[len(prefix):]
        ) and url[len(prefix):].startswith(record["sha256"] + "-"), "invalid public media URL")
        return record
    except (OSError, ValueError, KeyError, TypeError, SafetyError):
        return None


@contextmanager
def operation_lock(root):
    import fcntl

    state = private_directory(root / STATE_DIRECTORY)
    descriptor = os.open(state / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield state


def fingerprint(info):
    return {key: getattr(info, "st_" + key) for key in ("size", "mtime_ns", "ctime_ns", "dev", "ino")}


def category_for(relative):
    parts = relative.parts
    suffix = relative.suffix.lower()
    if any(part.startswith(".") for part in parts) or suffix in {".env", ".pem", ".key", ".p12"}:
        return "protected_runtime", False
    if suffix in {".db", ".sqlite", ".sqlite3"} or relative.name.endswith(("-wal", "-shm")):
        return "databases", False
    if suffix in {".log", ".bak", ".sql", ".gz", ".zip", ".tar"} or any(
        part.lower() in {"backups", "backup", "logs"} for part in parts
    ):
        return "backups_logs", False
    if parts[0] == "media":
        return "customer_attachments", False
    if len(parts) >= 3 and parts[0] == "uploads" and parts[1] in MEDIA_DIRECTORIES:
        return MEDIA_DIRECTORIES[parts[1]], suffix in MEDIA_EXTENSIONS
    if parts[0] == "uploads":
        return "documents_review", False
    return "other_runtime", False


def inventory(root, state, revision):
    summary = {}
    candidates = []
    skipped_links = 0
    excluded_directories = 0

    def walk_error(_error):
        raise SafetyError("inventory incomplete: directory could not be scanned")

    for directory, directories, files in os.walk(root, followlinks=False, onerror=walk_error):
        current = Path(directory)
        retained = []
        for name in sorted(directories):
            if (current / name).is_symlink():
                skipped_links += 1
            elif name in {STATE_DIRECTORY, "huggingface"}:
                excluded_directories += 1
            else:
                retained.append(name)
        directories[:] = retained
        for name in sorted(files):
            path = current / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                skipped_links += 1
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            relative = path.relative_to(root)
            category, candidate = category_for(relative)
            group = summary.setdefault(category, {"files": 0, "bytes": 0, "review_files": 0, "review_bytes": 0, "extensions": {}})
            group["files"] += 1
            group["bytes"] += info.st_size
            extension = relative.suffix.lower()
            if not re.fullmatch(r"\.[a-z0-9]{1,8}", extension):
                extension = "other"
            extension_group = group["extensions"].setdefault(extension, {"files": 0, "bytes": 0})
            extension_group["files"] += 1
            extension_group["bytes"] += info.st_size
            if candidate and info.st_nlink == 1 and info.st_size > 0:
                group["review_files"] += 1
                group["review_bytes"] += info.st_size
                candidates.append({"id": digest(relative.as_posix().encode()), "path": relative.as_posix(),
                                   "category": category, **fingerprint(info)})
    report = {"version": 1, "revision": revision, "root": str(root),
              "created_at": datetime.now(timezone.utc).isoformat(), "summary": summary,
              "skipped_symlinks": skipped_links, "excluded_directories": excluded_directories,
              "candidates": candidates, "approval": "all candidates require public-access review"}
    report_digest = digest(encoded(report))
    directory = private_directory(state / "inventories")
    save_json(directory / (report_digest + ".json"), report)
    return {"inventory_sha256": report_digest, "inventory": report}


def approved_files(root, state, request, revision):
    require(isinstance(request, dict) and request.get("confirm_public") is True, "public-access approval required")
    report_digest = request.get("inventory_sha256", "")
    require(isinstance(report_digest, str) and re.fullmatch(r"[a-f0-9]{64}", report_digest), "invalid inventory digest")
    report = load_json(state / "inventories" / (report_digest + ".json"))
    require(digest(encoded(report)) == report_digest, "inventory checksum mismatch")
    require(report["version"] == 1 and report["root"] == str(root) and report["revision"] == revision,
            "inventory does not match this deployment")
    if request.get("scope") == "public-media":
        require("file_ids" not in request, "choose a scope or explicit file IDs, not both")
        selected = report["candidates"]
        require(all(item["size"] <= MAX_BATCH_BYTES for item in selected), "a public file exceeds 512 MiB")
        return selected
    require("scope" not in request, "unsupported upload scope")
    ids = request.get("file_ids")
    require(isinstance(ids, list) and 0 < len(ids) <= 100 and all(
        isinstance(item, str) and re.fullmatch(r"[a-f0-9]{64}", item) for item in ids
    ), "select 1 to 100 inventory file IDs")
    require(len(set(ids)) == len(ids), "duplicate file IDs")
    candidates = {item["id"]: item for item in report["candidates"]}
    require(all(item in candidates for item in ids), "selection is not an inventory candidate")
    selected = [candidates[item] for item in ids]
    require(sum(item["size"] for item in selected) <= MAX_BATCH_BYTES, "batch exceeds 512 MiB")
    return selected


@contextmanager
def open_candidate(root, item):
    relative = PurePosixPath(item["path"])
    require(not relative.is_absolute() and ".." not in relative.parts and relative.parts,
            "invalid candidate path")
    category, candidate = category_for(relative)
    require(candidate and category == item["category"], "file category is not eligible")
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in relative.parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        file_descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
    finally:
        os.close(descriptor)
    with os.fdopen(file_descriptor, "rb") as stream:
        check_source(stream, item)
        yield stream
        check_source(stream, item)


def check_source(stream, item):
    info = os.fstat(stream.fileno())
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and fingerprint(info) == {
        key: item[key] for key in fingerprint(info)
    }, "source changed; create and approve a new inventory")


def file_digest(stream):
    result = hashlib.sha256()
    stream.seek(0)
    for chunk in iter(lambda: stream.read(PART_SIZE), b""):
        result.update(chunk)
    stream.seek(0)
    return result.hexdigest()


def verify_cdn(url, size, expected_digest):
    import httpx

    result = hashlib.sha256()
    received = 0
    with httpx.stream("GET", url, follow_redirects=False, timeout=60,
                      headers={"Accept-Encoding": "identity"}, trust_env=False) as response:
        require(response.status_code == 200, "CDN verification unavailable; retry the same selection")
        for chunk in response.iter_raw():
            received += len(chunk)
            require(received <= size, "CDN size mismatch")
            result.update(chunk)
    require(received == size and result.hexdigest() == expected_digest, "CDN content checksum mismatch")


def upload_one(client, settings, state, root, item):
    import fcntl

    locks = private_directory(state / "file-locks")
    descriptor = os.open(locks / (item["id"] + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = _upload_one(client, settings, state, root, item)
        index = private_directory(state / "public-index")
        record = {"source": item, "url": result["url"], "sha256": result["sha256"]}
        objects = private_directory(state / "public-objects")
        save_json(objects / (result["url"].rsplit("/", 1)[1] + ".json"), record)
        save_json(index / (item["id"] + ".json"), record)
        return {**result, "verification": "cdn_sha256"}


def _upload_one(client, settings, state, root, item):
    from qcloud_cos.cos_exception import CosServiceError

    receipts = private_directory(state / "uploads")
    with open_candidate(root, item) as stream:
        source_digest = file_digest(stream)
        check_source(stream, item)
        receipt_path = receipts / (digest(encoded({"id": item["id"], "source": source_digest,
                                                 "bucket": settings.cos_bucket})) + ".json")
        if receipt_path.exists():
            receipt = load_json(receipt_path)
            require(receipt["sha256"] == source_digest and receipt["size"] == item["size"], "upload record mismatch")
        else:
            receipt = {"sha256": source_digest, "size": item["size"], "phase": "pending", "parts": [],
                       "key": "intelligent-customer-service/assets/" + source_digest + "-" + uuid.uuid4().hex
                       + PurePosixPath(item["path"]).suffix.lower()}
            save_json(receipt_path, receipt)
        key = receipt["key"]
        require(re.fullmatch(r"intelligent-customer-service/assets/[a-f0-9]{64}-[a-f0-9]{32}\.[a-z0-9]+", key),
                "unsafe upload record")
        url = settings.cos_cdn_base_url.rstrip("/") + "/" + key
        if receipt["phase"] in {"uploaded", "verified"}:
            verify_cdn(url, item["size"], source_digest)
            receipt["phase"] = "verified"
            save_json(receipt_path, receipt)
            return {"file_id": item["id"], "url": url, "sha256": source_digest, "bytes": item["size"]}
        options = {"Bucket": settings.cos_bucket, "Key": key}
        content_type = mimetypes.guess_type(item["path"])[0] or "application/octet-stream"
        if item["size"] <= PART_SIZE:
            receipt["phase"] = "sending"
            save_json(receipt_path, receipt)
            try:
                client.put_object(**options, Body=stream, ContentType=content_type, EnableMD5=True,
                                  CacheControl="public, max-age=2592000, immutable", ForbidOverwrite="true")
            except CosServiceError as error:
                # A lost success response can leave an existing object; verify it without overwriting.
                if error.get_status_code() != 409:
                    raise
        else:
            if "upload_id" not in receipt:
                receipt["upload_id"] = client.create_multipart_upload(
                    **options, ContentType=content_type, CacheControl="public, max-age=2592000, immutable"
                )["UploadId"]
                save_json(receipt_path, receipt)
            # Local ETags avoid ListMultipartUploads/ListParts, which the upload-only key may deny.
            part_number = 0
            for chunk in iter(lambda: stream.read(PART_SIZE), b""):
                part_number += 1
                if part_number <= len(receipt["parts"]):
                    continue
                result = client.upload_part(**options, UploadId=receipt["upload_id"], PartNumber=part_number,
                                            Body=chunk, EnableMD5=True)
                receipt["parts"].append({"PartNumber": part_number, "ETag": result["ETag"]})
                save_json(receipt_path, receipt)
            check_source(stream, item)
            receipt["phase"] = "completing"
            save_json(receipt_path, receipt)
            try:
                client.complete_multipart_upload(**options, UploadId=receipt["upload_id"],
                                                 MultipartUpload={"Part": receipt["parts"]}, ForbidOverwrite="true")
            except CosServiceError as error:
                # Never request a CDN URL before completion: negative caching can hide a later upload.
                if error.get_error_code() != "NoSuchUpload" and error.get_status_code() != 409:
                    raise
        receipt["phase"] = "uploaded"
        save_json(receipt_path, receipt)
        check_source(stream, item)
        verify_cdn(url, item["size"], source_digest)
        receipt["phase"] = "verified"
        save_json(receipt_path, receipt)
        return {"file_id": item["id"], "url": url, "sha256": source_digest, "bytes": item["size"]}


def upload(root, state, request, revision, settings):
    selected = approved_files(root, state, request, revision)
    # Validate the entire selection before issuing any upload request.
    for item in selected:
        with open_candidate(root, item):
            pass
    client = None
    results = []
    for item in selected:
        with open_candidate(root, item):
            record = verified_public_record(root, item, settings) if request.get("scope") == "public-media" else None
        if record:
            result = {"file_id": item["id"], "url": record["url"], "sha256": record["sha256"],
                      "bytes": item["size"], "verification": "previously_verified"}
        else:
            if client is None:
                client = create_client(settings)
            result = upload_one(client, settings, state, root, item)
        results.append(result)
        print(json.dumps({"verified_file_id": item["id"], "bytes": item["size"]}), file=sys.stderr, flush=True)
    return {"inventory_sha256": request["inventory_sha256"], "verified": results}


def create_client(settings):
    require(settings.cos_bucket == "wohu-lanhua-cdn-1311448032" and settings.cos_region == "ap-shanghai"
            and settings.cos_cdn_base_url == "https://lanhua-cdn.hzwohu.com", "unexpected COS destination")
    require(settings.cos_secret_id.get_secret_value() and settings.cos_secret_key.get_secret_value(), "COS credentials unavailable")
    from qcloud_cos import CosConfig, CosS3Client

    # SDK diagnostics can contain signed headers. Other application logging must remain enabled.
    logging.getLogger("qcloud_cos").setLevel(logging.CRITICAL)
    return CosS3Client(CosConfig(Region=settings.cos_region, Scheme="https", Timeout=60,
                               SecretId=settings.cos_secret_id.get_secret_value(),
                               SecretKey=settings.cos_secret_key.get_secret_value()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inventory", "upload"))
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    try:
        require(re.fullmatch(r"[a-f0-9]{40}", args.revision), "invalid deployment revision")
        from app.core.config import get_settings

        settings = get_settings()
        root = Path(settings.upload_dir).parent
        require(root.is_dir() and not root.is_symlink(), "invalid production data root")
        root = root.resolve()
        require(root == Path("/app/data"), "COS operations are restricted to the production data mount")
        with operation_lock(root) as state:
            if args.action == "inventory":
                result = inventory(root, state, args.revision)
            else:
                payload = sys.stdin.buffer.read(65537)
                require(len(payload) <= 65536, "upload selection too large")
                result = upload(root, state, json.loads(payload), args.revision, settings)
        print(encoded(result).decode(), flush=True)
    except Exception as error:
        # Never print SDK exceptions, request objects, signed URLs or configuration values.
        message = str(error) if isinstance(error, SafetyError) else type(error).__name__
        print("COS operation failed: " + message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
