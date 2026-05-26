#!/usr/bin/env python3
"""Kennedy-Warren floorplan availability monitor."""
import json, os, pathlib, sys
import requests
from bs4 import BeautifulSoup

URL = (
    "https://kennedywarren.com/floorplans/_fp-renderable/"
    "params%3Ainstance%3D20f4d721f54a51b85d9fed8b7f6d8490"
    "%26action%3Drender%26type%3Dlisting-chunks/?forcecache=1"
)
HEADERS = {
    "x-requested-with": "XMLHttpRequest",
    "accept": "*/*",
    "user-agent": "Mozilla/5.0 (compatible; KWMonitor/1.0)",
    "referer": "https://kennedywarren.com/floorplans/",
}

# Comma-separated env var, e.g. "Historic 03,Historic 03A"
WATCH = [w.strip() for w in os.environ.get("WATCH_LIST", "Historic 03").split(",")]
STATE_FILE = pathlib.Path("state.json")

def fetch_available_titles() -> set[str]:
    r = requests.get(URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    # Each card's title <p class="jd-fp-card-info__title">Historic 05</p>
    titles = {p.get_text(strip=True) for p in soup.select("p.jd-fp-card-info__title")}
    if not titles:
        raise RuntimeError("Parsed zero floorplan titles — selector or endpoint changed.")
    return titles

def main() -> int:
    available = fetch_available_titles()
    # Exact-match (so "Historic 03" does NOT match "Historic 03A")
    currently_matching = {w for w in WATCH if w in available}

    prev = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    prev_matching = set(prev.get("matching", []))

    newly_available = currently_matching - prev_matching

    # Write state for the next run
    STATE_FILE.write_text(json.dumps({
        "all_available": sorted(available),
        "matching": sorted(currently_matching),
    }, indent=2))

    # GitHub Actions output
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as f:
            f.write(f"hit={'true' if newly_available else 'false'}\n")
            f.write(f"matches={', '.join(sorted(newly_available))}\n")
            f.write(f"all_matching={', '.join(sorted(currently_matching))}\n")
            f.write(f"available_count={len(available)}\n")

    print(f"Watch list:        {WATCH}")
    print(f"Available ({len(available)}): {sorted(available)}")
    print(f"Currently matching: {sorted(currently_matching)}")
    print(f"Newly available:    {sorted(newly_available)}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
