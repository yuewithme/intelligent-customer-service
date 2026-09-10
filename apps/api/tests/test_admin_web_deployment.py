from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_admin_proxy_does_not_grant_service_credentials():
    compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    nginx = (ROOT / "apps" / "admin" / "nginx.conf").read_text(encoding="utf-8")

    admin_service = compose.split("  admin-web:", 1)[1]
    assert "${BACKEND_ENV_FILE:-./deploy/env/backend.prod.env}" in admin_service
    assert "./apps/admin/nginx.conf:/etc/nginx/templates/default.conf.template:ro" in admin_service
    assert '${API_KEY}' not in nginx
    assert nginx.count('proxy_set_header Authorization $http_authorization;') == 2
    assert 'map $http_x_forwarded_proto $admin_forwarded_proto' in nginx
    assert 'https https;' in nginx
    assert nginx.count('proxy_set_header X-Forwarded-Proto $admin_forwarded_proto;') == 2
    for location in ('location /api/ {', 'location = /api/v1/admin/conversations/events {'):
        block = nginx.split(location, 1)[1].split('\n  }', 1)[0]
        assert 'proxy_set_header X-Forwarded-Proto $admin_forwarded_proto;' in block


def test_production_data_and_model_cache_are_externalized():
    compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")

    assert "${APP_DATA_DIR:-/srv/intelligent-customer-service/data}:/app/data" in compose
    assert (
        "${HF_CACHE_DIR:-/srv/intelligent-customer-service/cache/huggingface}"
        ":/app/data/huggingface"
    ) in compose
    assert '"21873:80"' not in compose


def test_external_callbacks_are_proxied_to_the_api():
    nginx = (ROOT / "apps" / "admin" / "nginx.conf").read_text(encoding="utf-8")

    for path in ("wechat", "eyun", "youzan"):
        assert f"location /{path}/" in nginx or f"location = /{path}/callback" in nginx
        assert f"proxy_pass http://api:8000/{path}/" in nginx
