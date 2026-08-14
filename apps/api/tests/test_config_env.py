from pathlib import Path

from app.core.config import PROJECT_ROOT, Settings


REPOSITORY_ROOT = PROJECT_ROOT.parents[1]
SCRIPT_ONLY_ENV_KEYS = {"EYUN_ACCOUNT", "EYUN_PASSWORD"}
PRODUCTION_REQUIRED_ENV_KEYS = {
    "APP_ENV",
    "API_AUTH_ENABLED",
    "API_KEY",
    "MCP_API_KEY",
    "ADMIN_GATE_PASSWORD",
    "ADMIN_GATE_SECRET",
    "APP_PUBLIC_BASE_URL",
    "DATABASE_URL",
    "CHAT_LOG_DB_URL",
    "UPLOAD_DIR",
    "WECHAT_TOKEN",
    "WECHAT_APP_ID",
    "WECHAT_APP_SECRET",
    "EYUN_BASE_URL",
    "EYUN_AUTHORIZATION",
    "EYUN_WID",
    "LLM_PROVIDER",
    "LLM_MODEL",
}


def _read_env_keys(path: Path) -> list[str]:
    keys = []
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.append(line.removeprefix("export ").split("=", 1)[0].strip())
    return keys


def _settings_env_keys() -> set[str]:
    return {
        str(field.alias or name.upper())
        for name, field in Settings.model_fields.items()
    }


def test_env_file_is_resolved_from_backend_root():
    configured = Path(Settings.model_config["env_file"])

    assert configured == PROJECT_ROOT / ".env"
    assert configured.is_absolute()


def test_config_module_does_not_require_repository_parents():
    source = (PROJECT_ROOT / "app" / "core" / "config.py").read_text(
        encoding="utf-8"
    )

    assert "PROJECT_ROOT.parents[" not in source


def test_legacy_eyun_http_origin_is_upgraded_to_https():
    settings = Settings(
        _env_file=None,
        eyun_base_url="http://www.eyunz.com/wx-api",
    )

    assert settings.eyun_base_url == "https://www.eyunz.com/wx-api"


def test_custom_eyun_http_origin_is_preserved():
    settings = Settings(
        _env_file=None,
        eyun_base_url="http://eyun.internal/wx-api",
    )

    assert settings.eyun_base_url == "http://eyun.internal/wx-api"


def test_backend_example_is_the_complete_settings_contract():
    keys = _read_env_keys(PROJECT_ROOT / ".env.example")

    assert len(keys) == len(set(keys))
    assert set(keys) == _settings_env_keys() | SCRIPT_ONLY_ENV_KEYS


def test_production_example_has_only_known_keys_and_required_overrides():
    keys = _read_env_keys(REPOSITORY_ROOT / "deploy/env/backend.prod.env.example")

    assert len(keys) == len(set(keys))
    assert set(keys) <= _settings_env_keys()
    assert PRODUCTION_REQUIRED_ENV_KEYS <= set(keys)


def test_frontend_tracked_env_files_contain_only_public_build_settings():
    admin_root = REPOSITORY_ROOT / "apps" / "admin"
    env_files = sorted(path.name for path in admin_root.glob(".env.*"))

    assert env_files == [".env.development", ".env.production"]
    for name in env_files:
        keys = _read_env_keys(admin_root / name)
        assert keys
        assert all(key.startswith("VITE_") for key in keys)
        assert not any(
            marker in key
            for key in keys
            for marker in ("API_KEY", "PASSWORD", "SECRET", "TOKEN")
        )
