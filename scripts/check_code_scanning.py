"""Fail releases with unresolved high/critical findings on the analyzed ref."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def blocking_alerts(pages: list[list[dict]]) -> list[dict]:
    return [
        alert
        for page in pages
        for alert in page
        if alert.get("state") == "open" and alert.get("rule", {}).get("security_severity_level") in {"high", "critical"}
    ]


def main() -> int:
    pages = json.loads(Path(sys.argv[1]).read_text())
    alerts = blocking_alerts(pages)
    for alert in alerts:
        print(f"Unresolved security alert #{alert['number']} ({alert['rule']['security_severity_level']})")
    print(f"High/critical open alerts on analyzed ref: {len(alerts)}")
    return int(bool(alerts))


if __name__ == "__main__":
    raise SystemExit(main())
