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

The three examples `ex-selfdb`, `ex-invoice` and `ex-fichero` in `pro/examples/` are views of those recorded traces. Their verdicts were produced by that older engine on 26 Oct 2026, before the window. They stay in the list and are labelled "Recorded 26 Oct 2026, before SLAY HACKS".

## Built during SLAY HACKS

New code, not present at the tag:

- `pro/` — the web UI, hosted provider, caps, result cache, injection detector, permalinks, rescue view, responsible-AI panel, and trace page.
- `scripts/pro_generate_examples.py` and `scripts/disclosure_stats.sh`.
- `GET /` and `GET /healthz`. `POST /judge` is unchanged and still requires a Nightshift token.
- `render.yaml`, `docs/RENDER.md`, and this file.
- The README section that introduces PhoenixRepo Pro. The older README text is still in the file so the recorded claims stay checkable.
- Tests under `tests/pro/`.
- Three examples recorded live during the window, on 17 Nov 2026 (below), and `scripts/pro_add_live_example.py`, which packs one.
- A small optional `relevance_client` argument on `judge()`, and `GEMINI_API_KEY` on settings so a Gemini key is redacted from traces.

`judge()` still runs the pre-existing scorer. The new hosted client is passed in. It does not replace the quote checks.

## Third-party parts

- Python, FastAPI, Uvicorn, Pydantic, httpx. Versions are pinned in `requirements.txt`. No new paid dependency was added.
- NVIDIA's OpenAI-compatible API at `https://integrate.api.nvidia.com/v1`, called through the existing `NvidiaDirect` client when `NVIDIA_API_KEY` is set. Default model id `nvidia/nemotron-3-ultra-550b-a55b` (the id the older notes say the API answered; `NVIDIA_MODEL` overrides it).
- Google Gemini's OpenAI-compatible endpoint, only when `GEMINI_API_KEY` is set and NVIDIA is missing or a call fails. Default model `gemini-2.5-flash`.
- GitHub's public REST API. `GITHUB_TOKEN` is optional. With no token the calls are unauthenticated.
- The browser's built-in serif and monospace fonts. No font file, CDN, or paid template is used.
- Render, if you deploy the Dockerfile. The free web service disk is ephemeral.

Pre-existing engine files were also edited inside the window, so they are not new files but they are changed work: `app/agents/relevance.py`, `api/run.py` (the old routes are hidden when `PHOENIX_HOSTED` is set), `scripts/judge_repo.py`, `scripts/score_candidates.py`, `config/settings.py` and the `Dockerfile`. `git diff pre-slayhacks -- app api config scripts Dockerfile` shows them.

## Examples recorded during the window

Three examples were judged live on 17 Nov 2026 (builds `59c9bc7` and `867f5b0`) with the hosted NVIDIA path, and packed with `scripts/pro_add_live_example.py`. They are listed first on the Pro page and carry the label "Recorded 17 Nov 2026, during SLAY HACKS". The older three stay below them.

| id | repository | verdict | model calls | wall time |
| --- | --- | --- | --- | --- |
| `live-bower` | `bower/bower` | ALREADY_EXISTS (`pnpm/pnpm`, verified quote), build `ba44ee7` | 3 | 102 s |
| `live-pspsps` | `melissaboiko/pspsps` | CANDIDATE, with a model-written rescue plan | 10 | 190 s |
| `live-request` | `request/request` | ALREADY_EXISTS (`sindresorhus/ky`, verified quote), build `1cd4b6b` | 3 | 86 s |
| `live-s3browser` | `powdahound/s3browser` | ALREADY_EXISTS (`awslabs/aws-js-s3-explorer`, verified quote), build `10c84b8` | 8 | 142 s |

Six judgements were run that day on build `59c9bc7`, one at a time. All six are listed here, including the three not packed:

- `melissaboiko/myougiden`: NEEDS_REVIEW, 6 calls, 155 s. Its prior-art link is a `qgithub.com` mirror address, not github.com, so it was not used.
- `powdahound/redis-collectd-plugin`: NEEDS_REVIEW, 2 calls, 59 s. A quote the model gave for the problem was not in the README, so the run stopped early. Not used.
- `melissaboiko/ansi`: NEEDS_REVIEW, 6 calls, 159 s. The only alternative with verified full overlap did not pass the maturity check, and the other was only partial overlap. It was not used.

None of the six came back ALREADY_EXISTS, and neither did the five runs on `867f5b0` below, so there is no such example from this window. The same repository can give a different verdict on another run: `powdahound/s3browser` came back ALREADY_EXISTS on an earlier build and NEEDS_REVIEW on the next two.

`live-bower` was replaced on 17 Nov by a run on build `ba44ee7` (see the last section); `live-s3browser` was replaced on 17 Nov by a run on build `10c84b8` (see below). `live-pspsps` was judged again on build `867f5b0` (the sealing fix), because the first copy had a deterministic rescue plan after a failed model call. The new copy has a rescue plan written by the model, and its trace is the server's own file, uncut. Five runs on `867f5b0`, one at a time: `s3browser` NEEDS_REVIEW (106 s, 6 calls), `redis-collectd-plugin` NEEDS_REVIEW (110 s, 6), `pspsps` CANDIDATE (190 s, 10), `myougiden` NEEDS_REVIEW (126 s, 6), `bower` NEEDS_REVIEW (94 s, 6). Because the four NEEDS_REVIEW verdicts were less definite than the copies already packed, `live-bower` (NO_GO, whose verified quote says the project is in maintenance mode) and `live-s3browser` were kept from the earlier build those two were not replaced.

Each packed trace passes `python scripts/verify_trace.py pro/examples/traces/<id>.jsonl --expected-run-hash <run_hash>`. Nothing in the stored JSON was edited by hand.

`scripts/disclosure_stats.sh` prints four counts: everything, code and tests with the packed examples and `data` excluded, the three 17 Nov examples with their traces, and the older packed examples plus data fixtures. The older packed traces are old engine output. The new code and the new tests are the other count.

### Build `10c84b8` (yes/no overlap rubric), six runs, 17 Nov 2026

Each repeated run used a fresh server and an empty cache. `s3browser`: ALREADY_EXISTS twice (142 s, 8 calls and 142 s, 6 calls), both blocked by `awslabs/aws-js-s3-explorer` with a verified quote. That run is the packed `live-s3browser`. `request/request`: ALREADY_EXISTS (`sindresorhus/got`, 101 s, 6 calls) and then NO_GO (134 s, 7 calls), because the second search did not return `got`, so it is not packed. `bower/bower`: NEEDS_REVIEW (94 s, 6 calls). `melissaboiko/myougiden`: NEEDS_REVIEW (146 s, 13 calls). The server trace of the second `s3browser` run failed `verify_trace.py` (parallel tool calls wrote lines out of chain order); that was a logging race, fixed in the same commit with a lock, and the packed run's trace verifies.

### Build `ba44ee7` (starred GitHub search, skipping immature alternatives), eight runs, 17 Nov 2026

Paired runs used a fresh server and an empty runtime dir each. `bower/bower`: ALREADY_EXISTS twice, both blocked by `pnpm/pnpm` with the same verified quote (102 s and 98 s, 3 calls each); the first is the packed `live-bower`. `powdahound/s3browser`: ALREADY_EXISTS twice (102 s and 74 s, 3 calls), but the blocking alternative differed (`qoomon/aws-s3-bucket-browser`, then `awslabs/aws-js-s3-explorer`), so it was not repacked. `request/request`: NO_GO three times (50, 61 and 74 s, 2–3 calls); every alternative was skipped as stale or immature and no verified quote was recorded, so it is not packed. `melissaboiko/myougiden`: NEEDS_PRIOR_ART (102 s, 2 calls), because all 15 alternatives were skipped. The GitHub candidate set was identical between paired runs.

### Build `1cd4b6b` (maturity gate independent of the model verdict), eight runs, 17 Nov 2026

Every run started a fresh server with an empty runtime dir, search cache included. `request/request`: ALREADY_EXISTS twice with the same blocker (`sindresorhus/ky`, verified quote; 86 s and 102 s, 3 calls each), and a third run that reused the first run's search cache gave the same verdict and blocker (94 s). The first run is the packed `live-request`. The candidate lists were not identical between runs (13 repositories each, 3 different), and `got` and `axios` were not among them. `powdahound/s3browser`: ALREADY_EXISTS (`qoomon/aws-s3-bucket-browser`, 98 s) and then NEEDS_REVIEW (110 s, all 15 skipped); `live-s3browser` was not changed. `bower/bower`: NEEDS_REVIEW (a model quote was not in the source, 94 s) and then ALREADY_EXISTS (`pnpm/pnpm`, 78 s); `live-bower` was not changed. `melissaboiko/myougiden`: NO_GO (86 s) because no clear user problem was found.

A later build seals the raw search, the problem statement, the queries, and the model replies for a repository and commit into one snapshot. A replay loads that snapshot and does not call the model, web search, GitHub search, or the metadata APIs. The sealed hash is the sha256 of the snapshot file itself. The result page and a packed example serve those exact bytes. A failed or `UNJUDGED` run does not keep a snapshot. Manifests are read at the commit, and OSV answers are stored with them. `scripts/pro_add_live_example.py --snapshot` copies the snapshot next to the example so the download on that page verifies. The snapshot's sha256 is a `SEARCH_SNAPSHOT` line in the trace, checked by `scripts/verify_trace.py --snapshot` and by Verify on the trace page. The snapshot JSON is downloaded from the run page. Model replies for that snapshot are cached by the sha256 of the prompt, the model id, and the temperature, so a replay is the same judgement. `?refresh_search=1` records a new snapshot.

