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


def client_ip(request: Request) -> str:
    """Who the daily cap applies to.

    Render's public edge sits behind Cloudflare, which sets CF-Connecting-IP
    and overwrites a client-supplied value. That header wins when it is present.
    Otherwise the last X-Forwarded-For hop is used, because Render appends the
    connecting address and a caller can prepend a fake list.
    """

    cloudflare = (request.headers.get("cf-connecting-ip") or "").strip()
    if cloudflare:
        return cloudflare[:64]
    forwarded = request.headers.get("x-forwarded-for", "")
    parts = [part.strip() for part in forwarded.split(",") if part.strip()]
    if parts:
        return parts[-1][:64]
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _error(exc: ProError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"error": {"type": exc.kind, "message": str(exc)}})


def ui_response():
    return FileResponse(UI_PATH, media_type="text/html; charset=utf-8")


@router.get("/pro/og.svg")
def og_image():
    return FileResponse(OG_PATH, media_type="image/svg+xml")


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


@router.get("/robots.txt")
def robots():
    from fastapi.responses import Response

    return Response("User-agent: *\nAllow: /\n", media_type="text/plain")


@router.get("/pro")
@router.get("/pro/run/{job_id}")
@router.get("/pro/r/{result_id}")
@router.get("/pro/r/{result_id}/rescue")
@router.get("/pro/r/{result_id}/trace")
def pro_page(result_id: str = ""):
    return ui_response()


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
