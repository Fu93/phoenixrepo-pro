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
| `PHOENIX_DAILY_RUNS_PER_IP` | no | Default 8. Taken when a run is admitted and refunded if no model is called. |
| `PHOENIX_MAX_QUEUE` | no | Default 4 waiting runs. A longer queue is HTTP 429. |
| `PHOENIX_DAILY_RUNS_GLOBAL` | no | Default 40 per UTC day. A run counts only after it calls a model. |
| `PHOENIX_PRO_DIR` | no | Cache and daily counters. Default `data/pro-runtime` inside the container. |
| `SAMPLE_MODE` | no | Leave `true`. It keeps `POST /run` on the sample investigation. Pro judging does not use it as a silent model. |

Do not put key values in the repo.

The daily cap reads `CF-Connecting-IP` only when `PHOENIX_TRUST_CF=true` (set in `render.yaml`, because Render's public edge sits behind Cloudflare). The value has to parse as an IP address. Otherwise the cap uses the last `X-Forwarded-For` hop that parses as an IP. A name or a fake prefix is ignored.

This assumes traffic arrives through Render and Cloudflare, which set or append those headers. A request that reaches the process directly can send a forged `X-Forwarded-For` and pick its own bucket. Do not expose the container port on the public internet.

## Free tier

A free web service spins down after about 15 minutes without traffic. The next request cold-starts. The filesystem is ephemeral. `PHOENIX_PRO_DIR` defaults to `data/pro-runtime` on that disk, so the result cache, the daily counters, and live traces are wiped on every restart, spin-down, and deploy. A repeat request is free only until that happens. Free web services cannot attach a persistent disk, so there is no durable place to point `PHOENIX_PRO_DIR` on the free plan. The three committed examples are in the image and survive a restart. `PHOENIX_PRO_STORE`, if set to a directory or an `https://` folder of JSON files, is an optional extra copy. A missing permalink says so instead of a bare error.

One service. No disk, no separate worker. Free instances do not scale horizontally.

## Dashboard

1. New Blueprint, point it at this repo, apply `render.yaml`.
2. Fill `NVIDIA_API_KEY` if you want live judging. Leave it empty to ship demo mode.
3. Open `/healthz`, then `/`. The examples load with no key.

The service URL is the permalink host. A result link is `/pro/r/<id>`.
