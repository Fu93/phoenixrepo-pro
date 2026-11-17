#!/usr/bin/env python3
"""Write pro/examples/*.json from recorded engine traces.

No model key is required. With --live and NVIDIA_API_KEY or GEMINI_API_KEY the
script also runs phoenix judge for each example repository and writes
pro/examples/live-<id>.json beside the recorded files. This environment had no
key, so the committed files are the recorded pack only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pro.pack import SPECS, write_all  # noqa: E402


def _live(out_dir: Path) -> int:
    from config.settings import Settings
    from pro.hosted import provider_mode
    from pro.service import ProService

    settings = Settings()
    if provider_mode(settings) == "demo":
        print("no NVIDIA_API_KEY or GEMINI_API_KEY; recorded examples were still written")
        return 0
    service = ProService(out_dir / "runtime", settings_fn=lambda: settings, background=False)
    failures = 0
    for spec in SPECS:
        url = f"https://github.com/{spec['replay']}"
        # Replay ids are not all owner/name. Use the packed repo url from the file just written.
        packed = json.loads((ROOT / "pro" / "examples" / f"{spec['id']}.json").read_text(encoding="utf-8"))
        url = packed.get("repo_url") or url
        try:
            body = service.submit(url, "generator", prefer_live=True)
        except Exception as exc:  # noqa: BLE001 — one repo should not hide the others
            print(f"{spec['id']}: {exc}")
            failures += 1
            continue
        print(f"{spec['id']}: {body.get('status')} {body.get('id') or body.get('result_id')}")
        if body.get("status") == "error":
            failures += 1
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Also judge the three repos when a hosted key is set.")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    written = write_all(args.out)
    for path in written:
        print(path)
    if args.live:
        return _live(args.out or (ROOT / "pro" / "examples"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
