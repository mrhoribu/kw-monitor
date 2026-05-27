#!/usr/bin/env python3
"""Kennedy-Warren floorplan monitor with diagnostics."""
import json, os, pathlib, re, sys
import requests
from bs4 import BeautifulSoup

URL = (
    "https://kennedywarren.com/floorplans/_fp-renderable/"
    "params%3Ainstance%3D20f4d721f54a51b85d9fed8b7f6d8490"
    "%26action%3Drender%26type%3Dlisting-chunks/?forcecache=1"
)

# Mimic a real browser more closely — the previous UA was too "bot-like"
HEADERS = {
    "x-requested-with": "XMLHttpRequest",
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "referer": "https://kennedywarren.com/floorplans/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "sec-ch-ua": '"Chromium";v="120", "Google Chrome";v="120", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "dnt": "1",
}

WATCH = [w.strip() for w in os.environ.get("WATCH_LIST", "Historic 03").split(",")]
FORCE_ALERT = os.environ.get("FORCE_ALERT", "").lower() in ("1", "true", "yes")
STATE_FILE = pathlib.Path("state.json")
DEBUG_DIR = pathlib.Path("debug"); DEBUG_DIR.mkdir(exist_ok=True)


def fetch():
    print("=" * 60)
    print("FETCH")
    print("=" * 60)
    print(f"URL: {URL}")
    r = requests.get(URL, headers=HEADERS, timeout=30, allow_redirects=True)
    print(f"Status:         {r.status_code}")
    print(f"Final URL:      {r.url}")
    print(f"Content-Type:   {r.headers.get('content-type')}")
    print(f"Content-Length: {len(r.content)} bytes")
    print(f"Redirected:     {len(r.history)} hops")
    for h in r.history:
        print(f"  -> {h.status_code} {h.url}")
    return r


def diagnose(html: str):
    print()
    print("=" * 60)
    print("RESPONSE INSPECTION")
    print("=" * 60)
    print(f"Total length: {len(html)} chars")
    print(f"Head (first 1500):\n{html[:1500]}")
    print(f"\nTail (last 500):\n{html[-500:]}")

    # Save full response as an artifact for download
    (DEBUG_DIR / "response.html").write_text(html)
    print(f"\nFull response written to debug/response.html")

    print()
    print("=" * 60)
    print("MARKER COUNTS")
    print("=" * 60)
    for m in ["<chunks>", "<template", "jd-fp-floorplan-card",
              "jd-fp-card-info__title", "Historic", "Historic 03",
              "Historic 05", "<!DOCTYPE", "<html", "Skip to main content"]:
        print(f"  {m!r}: {html.count(m)}")

    print()
    print("=" * 60)
    print("SELECTOR PROBES (html.parser)")
    print("=" * 60)
    soup = BeautifulSoup(html, "html.parser")
    for sel in [
        "p.jd-fp-card-info__title",
        "a.jd-fp-floorplan-card",
        "a[data-jd-fp-selector='floorplan-item']",
        "[data-floorplan]",
        "template",
        "chunks",
    ]:
        matches = soup.select(sel)
        sample = ""
        if matches:
            first = matches[0]
            sample = (f" | first: text={first.get_text(strip=True)[:50]!r}"
                      f" title={first.get('title','')!r}")
        print(f"  {sel}: {len(matches)}{sample}")

    print()
    print("=" * 60)
    print("REGEX EXTRACTION (parser-independent)")
    print("=" * 60)
    # Pull titles directly from the <a title="Floorplan X"> attribute — this
    # avoids any <template>/parser nonsense entirely.
    regex_titles = re.findall(r'<a[^>]+title="Floorplan ([^"]+)"', html)
    print(f"  Floorplan titles via regex: {len(regex_titles)}")
    if regex_titles:
        print(f"  Titles: {regex_titles}")
    return set(regex_titles)


def main():
    r = fetch()
    titles = diagnose(r.text)

    # Use regex result as source of truth (most robust)
    print()
    print("=" * 60)
    print("MATCHING")
    print("=" * 60)
    print(f"Watch list:         {WATCH}")
    print(f"Available ({len(titles)}): {sorted(titles)}")

    currently_matching = {w for w in WATCH if w in titles}
    print(f"Currently matching: {sorted(currently_matching)}")

    prev = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    prev_matching = set(prev.get("matching", []))

    if FORCE_ALERT:
        newly_available = currently_matching
        print("FORCE_ALERT=true — bypassing state check")
    else:
        newly_available = currently_matching - prev_matching
    print(f"Newly available:    {sorted(newly_available)}")

    STATE_FILE.write_text(json.dumps({
        "all_available": sorted(titles),
        "matching": sorted(currently_matching),
    }, indent=2))

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as f:
            f.write(f"hit={'true' if newly_available else 'false'}\n")
            f.write(f"matches={', '.join(sorted(newly_available))}\n")
            f.write(f"all_matching={', '.join(sorted(currently_matching))}\n")
            f.write(f"available_count={len(titles)}\n")

    if not titles:
        print("\nWARNING: zero floorplan titles parsed — see debug/response.html")
        # Don't raise — let the workflow upload the artifact

if __name__ == "__main__":
    main()
