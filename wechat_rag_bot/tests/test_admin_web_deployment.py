from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_admin_proxy_injects_api_key_at_runtime():
    compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    nginx = (ROOT / "admin-web" / "nginx.conf").read_text(encoding="utf-8")

    admin_service = compose.split("  admin-web:", 1)[1]
    assert "${BACKEND_ENV_FILE:-./deploy/env/backend.prod.env}" in admin_service
    assert "./admin-web/nginx.conf:/etc/nginx/templates/default.conf.template:ro" in admin_service
    assert nginx.count('proxy_set_header Authorization "Bearer ${API_KEY}";') == 2


def test_production_data_and_model_cache_are_externalized():
    compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")

    assert "${APP_DATA_DIR:-./runtime-data}:/app/data" in compose
    assert "${HF_CACHE_DIR:-./runtime-data/huggingface}:/app/data/huggingface" in compose
    assert '"21873:80"' not in compose
