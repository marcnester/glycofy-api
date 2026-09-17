from pathlib import Path

import yaml

from app.db import _database_url_with_required_tls

ROOT = Path(__file__).resolve().parents[1]


def test_render_blueprint_disables_origin_bypass() -> None:
    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text())
    service = blueprint["services"][0]

    assert service["domains"] == ["app.glycofy.ai"]
    assert service["renderSubdomainPolicy"] == "disabled"
    env = {item["key"]: item for item in service["envVars"]}
    assert env["EDGE_ORIGIN_SECRET"]["sync"] is False
    assert env["EDGE_ORIGIN_HEADER"]["value"] == "X-Glycofy-Edge-Auth"


def test_production_browser_allowlists_only_canonical_origin() -> None:
    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text())
    env = {item["key"]: item.get("value") for item in blueprint["services"][0]["envVars"]}

    assert env["ALLOWED_ORIGINS"] == "https://app.glycofy.ai"
    assert env["ALLOWED_HOSTS"] == "app.glycofy.ai"
    assert "onrender.com" not in env["ALLOWED_ORIGINS"]
    assert "onrender.com" not in env["ALLOWED_HOSTS"]


def test_production_postgres_requires_tls_without_overriding_stronger_configuration() -> None:
    plain = "postgresql+psycopg://app:secret@db.internal/glycofy"
    assert _database_url_with_required_tls(plain, production=True).endswith("?sslmode=require")
    verified = f"{plain}?sslmode=verify-full"
    assert _database_url_with_required_tls(verified, production=True) == verified
    assert _database_url_with_required_tls(plain, production=False) == plain


def test_only_nonsensitive_health_routes_bypass_edge_assertion() -> None:
    source = (ROOT / "app" / "main.py").read_text()

    assert 'request.url.path not in {"/health", "/ready"}' in source
    assert 'return reject("Origin access denied", 403, "origin_boundary_rejected")' in source
