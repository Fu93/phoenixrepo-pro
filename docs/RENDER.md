# Render

PhoenixRepo Pro is one web service built from the existing `Dockerfile`. The image listens on `0.0.0.0` and `$PORT`. Render sets `PORT`. TLS stops at Render's edge; the process speaks HTTP.

`render.yaml` is a Blueprint for a free web service. Health check path: `/healthz`. The same body is served at `/health`, which the image's own health check already uses.

## What to set

In the Dashboard, or as Blueprint env vars with `sync: false` so the value is typed there and not committed:

| Variable | Required | Role |
| --- | --- | --- |
| `NVIDIA_API_KEY` | for live judging | NVIDIA OpenAI-compatible API. No Nightshift token. |
| `NVIDIA_MODEL` | no | Default `nvidia/nemotron-3-ultra-550b-a55b`. |
| `GEMINI_API_KEY` | no | Used when NVIDIA is unset or a call fails. |
| `GEMINI_MODEL` | no | Default `gemini-2.5-flash`. |
| `GITHUB_TOKEN` | no | GitHub API. Unauthenticated if unset. |
| `PHOENIX_MAX_MODEL_CALLS` | no | Default 24 per run. |
| `PHOENIX_DAILY_RUNS_PER_IP` | no | Default 8. The first `X-Forwarded-For` hop is the address. A client can spoof that header off Render; the global cap still applies. |
| `PHOENIX_DAILY_RUNS_GLOBAL` | no | Default 40 per UTC day. |
| `SAMPLE_MODE` | no | Leave `true`. It keeps `POST /run` on the sample investigation. Pro judging does not use it as a silent model. |

Do not put key values in the repo.

## Free tier

A free web service spins down after about 15 minutes without traffic. The next request cold-starts. The filesystem is ephemeral: `data/pro-runtime/` (the result cache, daily counters, and live traces) is empty after a restart or spin-down. A repeat request is free only until that happens. The three committed examples are in the image and survive a restart.

One service. No disk, no separate worker. Free instances do not scale horizontally.

## Dashboard

1. New Blueprint, point it at this repo, apply `render.yaml`.
2. Fill `NVIDIA_API_KEY` if you want live judging. Leave it empty to ship demo mode.
3. Open `/healthz`, then `/`. The examples load with no key.

The service URL is the permalink host. A result link is `/pro/r/<id>`.
