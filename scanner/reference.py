"""Download and trim Travelpayouts reference lists.

Run once (and whenever new cities appear as codes on the page):
    python -m scanner.reference
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

URLS = {
    "cities.json": "https://api.travelpayouts.com/data/en/cities.json",
    "countries.json": "https://api.travelpayouts.com/data/en/countries.json",
}
REFERENCE_DIR = Path(__file__).resolve().parent.parent / "data" / "reference"


def trim(records: list[dict]) -> dict[str, dict]:
    """{code: {"name": ..., "country": ...}} keeping only what the page needs."""
    trimmed = {}
    for record in records:
        code = record.get("code")
        name = record.get("name") or (record.get("name_translations") or {}).get("en")
        if not code or not name:
            continue
        entry = {"name": name}
        if record.get("country_code"):
            entry["country"] = record["country_code"]
        trimmed[code] = entry
    return dict(sorted(trimmed.items()))


def main() -> None:
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    for filename, url in URLS.items():
        with urllib.request.urlopen(url, timeout=60) as response:
            records = json.loads(response.read().decode("utf-8"))
        data = trim(records)
        (REFERENCE_DIR / filename).write_text(
            json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        print(f"wrote {filename}: {len(data)} entries")


if __name__ == "__main__":
    main()
