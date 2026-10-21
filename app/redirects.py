"""Resolve redirects exclusively to fixed application destinations."""

from __future__ import annotations


def safe_return_path(raw: str | None, default: str | None = "/ui/index.html") -> str | None:
    if not isinstance(raw, str):
        return default
    match raw.split("?", 1)[0].split("#", 1)[0]:
        case "/ui/index.html":
            return "/ui/index.html"
        case "/ui/plan.html":
            return "/ui/plan.html"
        case "/ui/profile.html":
            return "/ui/profile.html"
        case "/ui/activities.html":
            return "/ui/activities.html"
        case "/ui/grocery.html":
            return "/ui/grocery.html"
        case "/ui/plan-week.html":
            return "/ui/plan-week.html"
        case "/ui/operations.html":
            return "/ui/operations.html"
        case _:
            return default
