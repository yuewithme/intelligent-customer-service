from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_admin_proxy_injects_api_key_at_runtime():
    compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    nginx = (ROOT / "admin-web" / "nginx.conf").read_text(encoding="utf-8")

    admin_service = compose.split("  admin-web:", 1)[1]
    assert "./deploy/env/backend.prod.env" in admin_service
    assert "./admin-web/nginx.conf:/etc/nginx/templates/default.conf.template:ro" in admin_service
    assert nginx.count('proxy_set_header Authorization "Bearer ${API_KEY}";') == 2
