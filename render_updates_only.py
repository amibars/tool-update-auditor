from __future__ import annotations

import json
from pathlib import Path

from update_audit import ClassifiedToolRecord, render_updates_only_markdown_report


def main() -> int:
    base_dir = Path(__file__).resolve().parent
    json_path = base_dir / "artifacts" / "tool-update-audit.json"
    updates_only_path = base_dir / "artifacts" / "tool-updates-only.md"
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    records = [ClassifiedToolRecord(**item) for item in payload]
    updates_only_path.write_text(render_updates_only_markdown_report(records), encoding="utf-8")
    print(f"Wrote updates-only Markdown to {updates_only_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
