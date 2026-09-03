import ast
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = API_ROOT / "app"
REPOSITORY_ROOT = API_ROOT.parents[1]


def test_repository_uses_the_canonical_top_level_layout():
    assert {path.name for path in REPOSITORY_ROOT.iterdir() if path.is_dir()} >= {
        "apps",
        "datasets",
        "deploy",
        "docs",
        "var",
    }
    assert not (REPOSITORY_ROOT / "wechat_rag_bot").exists()
    assert not (REPOSITORY_ROOT / "admin-web").exists()
    assert "var/" in (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8")


def test_legacy_layer_packages_are_removed():
    for package_name in ("services", "routers"):
        assert not (APP_ROOT / package_name).exists()
    assert not (APP_ROOT / "schemas").exists()


def test_production_modules_do_not_import_legacy_layer_packages():
    violations: list[str] = []
    legacy_prefixes = ("app.services", "app.routers", "app.schemas")

    for path in APP_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(legacy_prefixes):
                        violations.append(
                            f"{path.relative_to(APP_ROOT)}:{node.lineno} imports {alias.name}"
                        )
                continue
            if module.startswith(legacy_prefixes):
                violations.append(
                    f"{path.relative_to(APP_ROOT)}:{node.lineno} imports {module}"
                )

    assert violations == []
