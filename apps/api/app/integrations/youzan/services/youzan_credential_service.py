from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.infrastructure.database.models import Base, YouzanCredentialModel


class YouzanCredentialStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class YouzanCredentials:
    client_id: str
    client_secret: str
    kdt_id: str


_sessionmakers: dict[str, sessionmaker] = {}


def save_youzan_credentials(credentials: YouzanCredentials) -> dict[str, str | bool]:
    normalized = _normalize(credentials)
    encrypted_payload = _fernet().encrypt(
        json.dumps(
            {
                "client_id": normalized.client_id,
                "client_secret": normalized.client_secret,
                "kdt_id": normalized.kdt_id,
            },
            ensure_ascii=False,
        ).encode("utf-8")
    ).decode("ascii")
    now = datetime.now(timezone.utc)
    with _session() as session:
        record = session.get(YouzanCredentialModel, 1)
        if record is None:
            record = YouzanCredentialModel(
                id=1,
                encrypted_payload=encrypted_payload,
                updated_at=now,
            )
            session.add(record)
        else:
            record.encrypted_payload = encrypted_payload
            record.updated_at = now
        session.commit()
    _apply_to_settings(normalized)
    return credential_status()


def effective_youzan_credentials() -> YouzanCredentials | None:
    settings = get_settings()
    configured = _normalize(
        YouzanCredentials(
            client_id=settings.youzan_client_id,
            client_secret=settings.youzan_client_secret,
            kdt_id=settings.youzan_kdt_id,
        ),
        required=False,
    )
    if _complete(configured):
        return configured
    stored = _load_stored_credentials()
    if stored is not None:
        _apply_to_settings(stored)
    return stored


def credential_status() -> dict[str, str | bool]:
    credentials = effective_youzan_credentials()
    if credentials is None:
        return {
            "configured": False,
            "client_id_masked": "",
            "kdt_id": "",
        }
    return {
        "configured": True,
        "client_id_masked": _mask(credentials.client_id),
        "kdt_id": credentials.kdt_id,
    }


def reset_youzan_credential_store_for_tests() -> None:
    _sessionmakers.clear()


def _load_stored_credentials() -> YouzanCredentials | None:
    with _session() as session:
        record = session.scalar(select(YouzanCredentialModel).limit(1))
        if record is None:
            return None
        try:
            raw = _fernet().decrypt(record.encrypted_payload.encode("ascii"))
            payload = json.loads(raw.decode("utf-8"))
        except (
            InvalidToken,
            ValueError,
            UnicodeDecodeError,
        ) as exc:
            raise YouzanCredentialStoreError("有赞凭据无法解密，请重新配置") from exc
    if not isinstance(payload, dict):
        raise YouzanCredentialStoreError("有赞凭据格式无效，请重新配置")
    return _normalize(
        YouzanCredentials(
            client_id=str(payload.get("client_id") or ""),
            client_secret=str(payload.get("client_secret") or ""),
            kdt_id=str(payload.get("kdt_id") or ""),
        )
    )


def _session():
    database_url = get_settings().database_url
    factory = _sessionmakers.get(database_url)
    if factory is None:
        engine = create_engine(database_url)
        Base.metadata.create_all(engine, tables=[YouzanCredentialModel.__table__])
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[database_url] = factory
    return factory()


def _fernet() -> Fernet:
    settings = get_settings()
    secret = settings.youzan_credential_encryption_key.strip() or settings.api_key.strip()
    if secret and secret != "change_me":
        digest = hashlib.sha256(secret.encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(digest))
    return Fernet(_load_or_create_file_key())


def _load_or_create_file_key() -> bytes:
    key_path = (
        Path(get_settings().upload_dir).resolve().parent / ".youzan-credential.key"
    )
    key_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        file_descriptor = os.open(
            key_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        pass
    else:
        with os.fdopen(file_descriptor, "wb") as key_file:
            key_file.write(Fernet.generate_key())
            key_file.flush()
            os.fsync(key_file.fileno())
    try:
        os.chmod(key_path, 0o600)
        key = key_path.read_bytes().strip()
        Fernet(key)
    except (OSError, ValueError) as exc:
        raise YouzanCredentialStoreError("有赞凭据加密密钥文件不可用") from exc
    return key


def _apply_to_settings(credentials: YouzanCredentials) -> None:
    settings = get_settings()
    settings.youzan_client_id = credentials.client_id
    settings.youzan_client_secret = credentials.client_secret
    settings.youzan_kdt_id = credentials.kdt_id


def _normalize(
    credentials: YouzanCredentials,
    *,
    required: bool = True,
) -> YouzanCredentials:
    normalized = YouzanCredentials(
        client_id=credentials.client_id.strip(),
        client_secret=credentials.client_secret.strip(),
        kdt_id=credentials.kdt_id.strip(),
    )
    if required and not _complete(normalized):
        raise YouzanCredentialStoreError("CLIENT_ID、CLIENT_SECRET、KDT_ID 必须完整配置")
    return normalized


def _complete(credentials: YouzanCredentials) -> bool:
    return bool(
        credentials.client_id and credentials.client_secret and credentials.kdt_id
    )


def _mask(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"
