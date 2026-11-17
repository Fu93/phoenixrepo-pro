"""Publish dates and download counts for npm and PyPI alternatives.

A missing response leaves the fields unknown. Nothing here writes a stand-in number.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

# A recent release is the same 12-month window as a GitHub push.
RELEASE_DAYS = 365
# Weekly downloads that stand in for the "widely used" half of the star rule.
NPM_WEEKLY_DOWNLOADS = 10000

# Docs hosts whose product is one registry package. Used only after that package
# has a real, recent release. An unmapped host stays unknown.
DOCS_TO_NPM = {
    "yarnpkg.com": "yarn",
    "classic.yarnpkg.com": "yarn",
    "docs.npmjs.com": "npm",
}

_NPM_URL = re.compile(r"(?:www\.)?npmjs\.com/package/([^/\s?#]+)", re.IGNORECASE)
_PYPI_URL = re.compile(r"pypi\.org/project/([^/\s?#]+)", re.IGNORECASE)


def npm_name(row: dict[str, Any]) -> str | None:
    for field in ("url", "alternative_id"):
        text = str(row.get(field) or "")
        if text.startswith("web:"):
            text = text[4:]
        if text.startswith("npm:"):
            return text[4:].strip() or None
        match = _NPM_URL.search(text)
        if match:
            return match.group(1)
    host = _host(row)
    mapped = DOCS_TO_NPM.get(host)
    if mapped:
        return mapped
    return None


def pypi_name(row: dict[str, Any]) -> str | None:
    for field in ("url", "alternative_id"):
        text = str(row.get(field) or "")
        if text.startswith("web:"):
            text = text[4:]
        if text.startswith("pypi:"):
            return text[5:].strip() or None
        match = _PYPI_URL.search(text)
        if match:
            return match.group(1).strip("/")
    return None


def _host(row: dict[str, Any]) -> str:
    for field in ("url", "alternative_id"):
        text = str(row.get(field) or "")
        if text.startswith("web:"):
            text = text[4:]
        if "://" not in text:
            continue
        host = (urlparse(text).hostname or "").casefold()
        if host.startswith("www."):
            host = host[4:]
        return host
    return ""


def _parse_date(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _recent(value: str, *, now: datetime) -> bool:
    parsed = _parse_date(value)
    if parsed is None:
        return False
    return (now - parsed).days <= RELEASE_DAYS


def _http_get_json(url: str) -> Any:
    import httpx

    with httpx.Client(timeout=8.0) as client:
        response = client.get(url)
    if response.status_code != 200:
        raise ValueError(f"HTTP {response.status_code} for {url}")
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("registry response was not an object")
    return data


def attach_registry_metadata(
    rows: list[dict[str, Any]],
    *,
    getter: Callable[[str], Any] | None = None,
    trace: Any | None = None,
    limit: int = 5,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Fill release dates and, for npm, weekly downloads. At most ``limit`` packages."""

    if getter is None:
        getter = _http_get_json
    clock = now or datetime.now(timezone.utc)
    chosen: list[tuple[dict[str, Any], str, str]] = []
    for row in rows:
        if len(chosen) >= limit:
            break
        if row.get("registry_checked"):
            continue
        npm = npm_name(row)
        if npm:
            chosen.append((row, "npm", npm))
            continue
        pypi = pypi_name(row)
        if pypi:
            chosen.append((row, "pypi", pypi))

    def one(item: tuple[dict[str, Any], str, str]) -> None:
        row, kind, name = item
        status = "ok"
        error = ""
        try:
            if kind == "npm":
                _fill_npm(row, name, getter, clock)
            else:
                _fill_pypi(row, name, getter, clock)
        except Exception as exc:  # noqa: BLE001 — a registry miss is unknown, not zero
            row["registry_checked"] = True
            row["registry_status"] = "unavailable"
            status = "metadata unavailable"
            error = str(exc)[:180]
        if trace is not None:
            trace.event(
                agent_name="orchestrator",
                action="REGISTRY_METADATA",
                input_summary={"package": name, "registry": kind},
                output_summary={
                    "status": status,
                    "released_at": row.get("released_at") or "",
                    "weekly_downloads": row.get("weekly_downloads"),
                    "error": error,
                },
                status="ok" if status == "ok" else "error",
            )

    from pro.pace import map_ordered

    map_ordered(one, chosen, workers=4)
    return rows


def _fill_npm(row: dict[str, Any], name: str, getter: Callable[[str], Any], now: datetime) -> None:
    meta = getter(f"https://registry.npmjs.org/{name}")
    if not isinstance(meta, dict):
        raise ValueError("npm registry did not return an object")
    times = meta.get("time") if isinstance(meta.get("time"), dict) else {}
    latest = str((meta.get("dist-tags") or {}).get("latest") or "")
    released = str(times.get(latest) or times.get("modified") or "")
    downloads = None
    try:
        point = getter(f"https://api.npmjs.org/downloads/point/last-week/{name}")
        if isinstance(point, dict) and isinstance(point.get("downloads"), int):
            downloads = point["downloads"]
    except Exception:
        downloads = None
    row["registry_checked"] = True
    row["registry_status"] = "ok"
    row["registry_package"] = name
    licence = meta.get("license")
    spdx = licence.get("type") if isinstance(licence, dict) else licence
    spdx = str(spdx or "").strip()
    if spdx and spdx.casefold() not in {"unlicensed", "see license in license", "none"} and not row.get("licence_spdx"):
        row["licence_spdx"] = spdx
    if released:
        row["released_at"] = released
    if downloads is not None:
        row["weekly_downloads"] = downloads
    official = _host(row) in DOCS_TO_NPM
    recent = bool(released) and _recent(released, now=now)
    if recent and (official or (downloads is not None and downloads >= NPM_WEEKLY_DOWNLOADS)):
        row["registry_mature"] = True
        why = f"npm:{name} released {released[:10]}"
        if downloads is not None:
            why += f", {downloads} downloads last week"
        if official:
            why += ", official docs host"
        row["registry_reason"] = why
    else:
        row["registry_mature"] = False


def _fill_pypi(row: dict[str, Any], name: str, getter: Callable[[str], Any], now: datetime) -> None:
    payload = getter(f"https://pypi.org/pypi/{name}/json")
    if not isinstance(payload, dict):
        raise ValueError("PyPI did not return an object")
    version = str((payload.get("info") or {}).get("version") or "")
    releases = payload.get("releases") if isinstance(payload.get("releases"), dict) else {}
    files = releases.get(version) if isinstance(releases.get(version), list) else []
    released = ""
    if files and isinstance(files[0], dict):
        released = str(files[0].get("upload_time") or files[0].get("upload_time_iso_8601") or "")
    row["registry_checked"] = True
    row["registry_status"] = "ok"
    row["registry_package"] = name
    if released:
        row["released_at"] = released
    # A PyPI date can show the package is maintained. It is not a star count.
    row["registry_mature"] = False
    if released and not _recent(released, now=now):
        row["registry_reason"] = f"pypi:{name} last release {released[:10]} is older than {RELEASE_DAYS} days"
