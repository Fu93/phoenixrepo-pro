"""Dependency advisories from OSV.dev. No key."""

from __future__ import annotations

import json
from typing import Any

import httpx

OSV_QUERY = "https://api.osv.dev/v1/querybatch"


def query_batch(packages: list[dict[str, str]], *, transport: httpx.BaseTransport | None = None) -> list[dict[str, Any]]:
    """Pinned packages are queried with their version. Unpinned ones are not flagged."""

    if not packages:
        return []
    rows: list[dict[str, Any]] = []
    pinned = []
    for item in packages:
        version = str(item.get("version") or "").strip()
        if not version:
            rows.append({
                "name": item["name"],
                "ecosystem": item["ecosystem"],
                "version": "",
                "note": "version unknown",
                "ids": [],
            })
            continue
        pinned.append(item)
    if not pinned:
        return rows
    body = {
        "queries": [
            {"version": item["version"], "package": {"name": item["name"], "ecosystem": item["ecosystem"]}}
            for item in pinned
        ]
    }
    try:
        with httpx.Client(timeout=15.0, transport=transport) as client:
            response = client.post(OSV_QUERY, json=body)
    except httpx.HTTPError:
        return rows
    if response.status_code != 200:
        return rows
    try:
        payload = response.json()
    except ValueError:
        return rows
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return rows
    for item, result in zip(pinned, results):
        vulns = result.get("vulns") if isinstance(result, dict) else None
        if not vulns:
            continue
        ids = [str(v.get("id")) for v in vulns if isinstance(v, dict) and v.get("id")]
        rows.append({
            "name": item["name"],
            "ecosystem": item["ecosystem"],
            "version": item.get("version") or "",
            "ids": ids[:8],
        })
    return rows


def parse_requirements(text: str) -> list[dict[str, str]]:
    rows = []
    for line in text.splitlines():
        raw = line.split("#", 1)[0].strip()
        if not raw or raw.startswith("-"):
            continue
        name, version = raw, ""
        if "==" in raw:
            name, version = raw.split("==", 1)
            version = version.split(",")[0].strip()
        name = name.split(">=")[0].split("<=")[0].split("~=")[0].split("!=")[0].split("[")[0].strip()
        if not name:
            continue
        # Only an exact == pin is a version. A range is not sent to OSV.
        if "==" not in raw:
            version = ""
        rows.append({"name": name, "ecosystem": "PyPI", "version": version})
    return rows


def advisories_for_repo(repo_id: str, *, getter=None, transport: httpx.BaseTransport | None = None) -> list[dict[str, Any]]:
    """Best-effort. A network failure returns an empty list and does not change the verdict."""

    fetch = getter or _github_text
    packages: list[dict[str, str]] = []
    requirements = fetch(repo_id, "requirements.txt")
    if requirements:
        packages.extend(parse_requirements(requirements))
    package_json = fetch(repo_id, "package.json")
    if package_json:
        packages.extend(parse_package_json(package_json))
    return query_batch(packages[:30], transport=transport)


def _github_text(repo_id: str, path: str) -> str:
    url = f"https://raw.githubusercontent.com/{repo_id}/HEAD/{path}"
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url)
    except httpx.HTTPError:
        return ""
    if response.status_code != 200:
        return ""
    return response.text


def parse_package_json(text: str) -> list[dict[str, str]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    rows = []
    for key in ("dependencies", "devDependencies"):
        deps = data.get(key) if isinstance(data, dict) else None
        if isinstance(deps, dict):
            for name, spec in deps.items():
                text = spec.strip() if isinstance(spec, str) else ""
                version = text if text[:1].isdigit() else ""
                rows.append({"name": name, "ecosystem": "npm", "version": version})
    return rows
