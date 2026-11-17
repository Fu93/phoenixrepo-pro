# Disclosure

PhoenixRepo Pro is a SLAY HACKS entry. The rules ask for original work inside the build window and forbid presenting older work as new. This file is the split.

Window: 7 November 2026 through 7 December 2026, 22:00 London.

Tag `pre-slayhacks` is commit `8ff6a20`. It is the whole repository as it stood before that window. `scripts/disclosure_stats.sh` prints `git diff --stat pre-slayhacks`.

## Already in the repository at `pre-slayhacks`

This is not new work for SLAY HACKS. It is the PhoenixRepo codebase the tag points at, including:

- Gate 1: `scripts/judge_repo.py`, `scripts/score_candidates.py`, `app/agents/relevance.py` (problem extraction, prior-art search, quote checks, verdicts).
- Gate 2: `app/rescue_plan.py`, `app/revive_loop.py`, `scripts/revive.py`, and the committed fichero rescue plan.
- The NVIDIA client `app/adapters/nvidia.py` and the Nightshift client `app/adapters/meterless.py`.
- Hash-chained traces: `app/trace_log.py`, `scripts/verify_trace.py`, and the recorded replays in `data/replays/`.
- The replay page at `/ui`.
- The investigation API `POST /run`, sample mode, and the existing test suite.
- The scored-run numbers, audits, and the previous README sections kept under the PhoenixRepo record.

The three examples in `pro/examples/` are views of those recorded traces. The verdicts were produced by that older engine. Packing them into the Pro page is new. Running a model again to produce them was not possible in the build environment: no `NVIDIA_API_KEY` or `GEMINI_API_KEY` was available. `scripts/pro_generate_examples.py --live` is the command that does call the model when a key is set.

## Built during SLAY HACKS

New code, not present at the tag:

- `pro/` — the web UI, hosted provider, caps, result cache, injection detector, permalinks, rescue view, responsible-AI panel, and trace page.
- `scripts/pro_generate_examples.py` and `scripts/disclosure_stats.sh`.
- `GET /` and `GET /healthz`. `POST /judge` is unchanged and still requires a Nightshift token.
- `render.yaml`, `docs/RENDER.md`, and this file.
- The README section that introduces PhoenixRepo Pro. The older README text is still in the file so the recorded claims stay checkable.
- Tests under `tests/pro/`.
- A small optional `relevance_client` argument on `judge()`, and `GEMINI_API_KEY` on settings so a Gemini key is redacted from traces.

`judge()` still runs the pre-existing scorer. The new hosted client is passed in. It does not replace the quote checks.

## Third-party parts

- Python, FastAPI, Uvicorn, Pydantic, httpx. Versions are pinned in `requirements.txt`. No new paid dependency was added.
- NVIDIA's OpenAI-compatible API at `https://integrate.api.nvidia.com/v1`, called through the existing `NvidiaDirect` client when `NVIDIA_API_KEY` is set. Default model id `nvidia/nemotron-3-ultra-550b-a55b` (the id the older notes say the API answered; `NVIDIA_MODEL` overrides it).
- Google Gemini's OpenAI-compatible endpoint, only when `GEMINI_API_KEY` is set and NVIDIA is missing or a call fails. Default model `gemini-2.5-flash`.
- GitHub's public REST API. `GITHUB_TOKEN` is optional. With no token the calls are unauthenticated.
- The browser's built-in serif and monospace fonts. No font file, CDN, or paid template is used.
- Render, if you deploy the Dockerfile. The free web service disk is ephemeral.

Live judgements have been run from this branch with an NVIDIA key: one took 322 seconds and 15 model calls and returned CANDIDATE; another returned NEEDS_REVIEW because a problem quote did not match the README. Those live JSON files are not all committed. The three examples in `pro/examples/` are still the 26 Oct 2026 recordings.

`scripts/disclosure_stats.sh` prints three counts: everything, code and tests with `pro/examples` and `data` excluded, and the packed examples plus data fixtures on their own. The packed traces are old engine output. The new code and the new tests are the other count.
