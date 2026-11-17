"""HTTP routes for PhoenixRepo Pro. The existing /judge route is unchanged."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from config.settings import Settings
from pro.injection import attack_table, neutralise, pipeline_check
from pro.service import ProError, ProService

ROOT = Path(__file__).resolve().parents[1]
UI_PATH = Path(__file__).resolve().parent / "ui" / "index.html"
OG_PATH = Path(__file__).resolve().parent / "ui" / "og.svg"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "injected_readme.md"

router = APIRouter()
SERVICE: ProService | None = None


class RunBody(BaseModel):
    url: str
    prefer_live: bool = False


def state_dir() -> Path:
    """Cache, daily counters, and live results. Override with PHOENIX_PRO_DIR."""

    return Path(os.environ.get("PHOENIX_PRO_DIR") or (ROOT / "data" / "pro-runtime"))


def get_service() -> ProService:
    global SERVICE
    if SERVICE is None:
        SERVICE = ProService(state_dir(), settings_fn=Settings)
    return SERVICE


def _parse_ip(value: str) -> str:
    import ipaddress

    text = (value or "").strip()
    if not text:
        return ""
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return ""


def client_ip(request: Request) -> str:
    """Who the daily cap applies to.

    CF-Connecting-IP is trusted only when PHOENIX_TRUST_CF=true (Render's public
    edge, which sits behind Cloudflare). Otherwise the last X-Forwarded-For hop
    that parses as an IP address is used. Anything that is not an IP is ignored.
    """

    import os

    if os.environ.get("PHOENIX_TRUST_CF", "").strip().lower() in {"1", "true", "yes"}:
        cloudflare = _parse_ip(request.headers.get("cf-connecting-ip") or "")
        if cloudflare:
            return cloudflare
    forwarded = request.headers.get("x-forwarded-for", "")
    parts = [part.strip() for part in forwarded.split(",") if part.strip()]
    for part in reversed(parts):
        parsed = _parse_ip(part)
        if parsed:
            return parsed
    if request.client and request.client.host:
        parsed = _parse_ip(request.client.host)
        if parsed:
            return parsed
    return "unknown"


def _error(exc: ProError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"error": {"type": exc.kind, "message": str(exc)}})


def ui_response(request: Request | None = None, result_id: str = ""):
    from fastapi.responses import HTMLResponse

    base = ""
    path = "/pro"
    if request is not None:
        import os

        base = (os.environ.get("PUBLIC_BASE_URL") or str(request.base_url)).strip().rstrip("/")
        path = request.url.path
    html = UI_PATH.read_text(encoding="utf-8")
    image = f"{base}/pro/og.png" if base else "/pro/og.png"
    url = f"{base}{path}" if base else path
    title = f"PhoenixRepo Pro · {result_id}" if result_id else "PhoenixRepo Pro"
    html = html.replace("__OG_IMAGE__", image).replace("__OG_URL__", url).replace("__OG_TITLE__", title)
    return HTMLResponse(html)


@router.get("/pro/og.svg")
def og_image():
    return FileResponse(OG_PATH, media_type="image/svg+xml")


@router.get("/og.png")
@router.get("/pro/og.png")
def og_png(title: str = "PhoenixRepo Pro"):
    from fastapi.responses import Response

    from pro.brand import og_png as render_png

    return Response(render_png(title), media_type="image/png")


@router.get("/favicon.png")
def favicon():
    from fastapi.responses import Response

    from pro.brand import favicon_png

    return Response(favicon_png(), media_type="image/png")


@router.get("/favicon.ico")
def favicon_ico():
    from fastapi.responses import Response

    from pro.brand import favicon_ico as render_ico

    return Response(render_ico(), media_type="image/x-icon")


@router.get("/robots.txt")
def robots():
    from fastapi.responses import Response

    return Response("User-agent: *\nAllow: /\n", media_type="text/plain")


@router.get("/pro")
@router.get("/pro/run/{job_id}")
@router.get("/pro/r/{result_id}")
@router.get("/pro/r/{result_id}/rescue")
@router.get("/pro/r/{result_id}/trace")
def pro_page(request: Request, result_id: str = ""):
    return ui_response(request, result_id)


@router.get("/pro/api/status")
def pro_status():
    return get_service().status()


@router.get("/pro/api/examples")
def pro_examples():
    return {"examples": get_service().examples.cards()}


@router.get("/pro/api/examples/{example_id}")
@router.get("/pro/api/results/{example_id}")
def pro_result(example_id: str):
    try:
        return get_service().get_result(example_id)
    except ProError as exc:
        return _error(exc)


@router.post("/pro/api/runs")
def pro_start(body: RunBody, request: Request):
    try:
        return get_service().submit(body.url, client_ip(request), prefer_live=body.prefer_live)
    except ProError as exc:
        return _error(exc)


@router.get("/pro/api/runs/{job_id}")
def pro_job(job_id: str):
    try:
        return get_service().get_job(job_id)
    except ProError as exc:
        return _error(exc)


@router.get("/pro/api/runs/{job_id}/events")
def pro_events(job_id: str):
    service = get_service()
    if job_id not in service.jobs:
        return JSONResponse(status_code=404, content={"error": {"type": "NotFound", "message": "Unknown run."}})

    def generate():
        for event in service.iter_events(job_id):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/pro/api/injection-demo")
def injection_demo():
    text = FIXTURE.read_text(encoding="utf-8")
    cleaned, report = neutralise(text)
    checked = pipeline_check(text)
    return {
        "original": text,
        "neutralised": cleaned,
        "report": report,
        "pipeline": checked,
        "attacks": attack_table(),
    }


@router.get("/pro/api/results/{result_id}/rescue.md")
def rescue_markdown(result_id: str):
    from fastapi.responses import Response

    try:
        result = get_service().get_result(result_id)
    except ProError as exc:
        return _error(exc)
    return Response(_rescue_markdown(result), media_type="text/markdown; charset=utf-8")


@router.get("/pro/api/triage")
def triage(user: str):
    from pro.triage import TriageError, abandoned_repos

    try:
        return {"user": user, "repos": abandoned_repos(user)}
    except TriageError as exc:
        return JSONResponse(status_code=exc.status, content={"error": {"type": "Triage", "message": str(exc)}})


def _rescue_markdown(result: dict) -> str:
    lines = [f"# Rescue plan: {result.get('repo_id') or result.get('id')}", ""]
    if not result.get("rescue"):
        lines.append(result.get("rescue_note") or "No rescue plan.")
        return "\n".join(lines) + "\n"
    lines.append(result.get("rescue_note") or "")
    lines.append("")
    for stage in result["rescue"].get("stages") or []:
        lines.append(f"## {stage.get('stage')}")
        lines.append("")
        for item in stage.get("items") or []:
            lines.append(f"### {item.get('title')}")
            lines.append("")
            lines.append(item.get("why") or "")
            lines.append("")
            lines.append(f"> {item.get('quote') or ''}")
            lines.append("")
    return "\n".join(lines) + "\n"
