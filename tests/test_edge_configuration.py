from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_render_blueprint_disables_origin_bypass() -> None:
    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text())
    service = blueprint["services"][0]

    assert service["domains"] == ["app.glycofy.ai"]
    assert service["renderSubdomainPolicy"] == "disabled"


def test_production_browser_allowlists_only_canonical_origin() -> None:
    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text())
    env = {item["key"]: item.get("value") for item in blueprint["services"][0]["envVars"]}

    assert env["ALLOWED_ORIGINS"] == "https://app.glycofy.ai"
    assert env["ALLOWED_HOSTS"] == "app.glycofy.ai"
    assert "onrender.com" not in env["ALLOWED_ORIGINS"]
    assert "onrender.com" not in env["ALLOWED_HOSTS"]
