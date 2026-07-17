from pathlib import Path

from app.config import PROJECT_ROOT, Settings


def test_env_file_is_resolved_from_backend_root():
    configured = Path(Settings.model_config["env_file"])

    assert configured == PROJECT_ROOT / ".env"
    assert configured.is_absolute()
