#!/usr/bin/env python3
"""
Kennedy-Warren floorplan availability monitor.

Polls a Jonah Systems render endpoint for currently-available floorplans,
matches against a watch list, and emits GitHub Actions outputs that fire
notification steps on state transitions (not-available → available).

Behavior:
  - Writes last-run.txt every run so the repo stays active (60-day rule).
  - Retries network errors up to 3 times with progressive backoff.
  - Exits 0 on persistent network errors so transient failures don't
    pollute the workflow run history or fire failure notifications.
  - Saves full response to debug/response.html for post-hoc inspection.
  - State tracking via state.json — only alerts on transitions.

Environment variables:
  WATCH_LIST  : comma-separated floorplan names, e.g. "Historic 03,Historic 05"
  FORCE_ALERT : "1"/"true" to bypass state check (useful for testing)
"""
import json, os, pathlib, re, time
from datetime import datetime, timezone
import requests
from requests.exceptions import ConnectTimeout, ConnectionError, ReadTimeout

URL = (
    "https://kennedywarren.com/floorplans/_fp-renderable/"
    "params%3Ainstance%3D20f4d721f54a51b85d9fed8b7f6d8490"
    "%26action%3Drender%26type%3Dlisting-chunks/?forcecache=1"
)

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
HEARTBEAT_FILE = pathlib.Path("last-run.txt")
DEBUG_DIR = pathlib.Path("debug")
DEBUG_DIR.mkdir(exist_ok=True)


def write_heartbeat() -> None:
    """Write timestamp so the repo stays active (60-day inactivity rule)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    server = os.environ.get("GITHUB_SERVER_URL", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_url = (
        f"{server}/{repo}/actions/runs/{run_id}"
        if server and repo and run_id != "local" else ""
    )
    HEARTBEAT_FILE.write_text(
        f"last_run: {now}\nrun_id:   {run_id}\nrun_url:  {run_url}\n"
    )
    print(f"[heartbeat] {now} (run {run_id})")


def fetch(max_attempts: int = 3):
    """Fetch the endpoint with retries. Returns Response or None on failure."""
    print(f"\n[fetch] {URL}")
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            r = requests.get(URL, headers=HEADERS, timeout=30, allow_redirects=True)
            print(f"[fetch] HTTP {r.status_code}, {len(r.content)} bytes, "
                  f"content-type={r.headers.get('content-type')}")
            for h in r.history:
                print(f"[fetch] redirect: {h.status_code} -> {h.url}")
            return r
        except (ConnectTimeout, ConnectionError, ReadTimeout) as e:
            last_err = e
            wait = 5 * attempt   # 5s, 10s, 15s
            print(f"[fetch] attempt {attempt}/{max_attempts} failed: "
                  f"{type(e).__name__}: {e}")
            if attempt < max_attempts:
                print(f"[fetch] retrying in {wait}s")
                time.sleep(wait)
    print(f"[fetch] all {max_attempts} attempts failed; last error: {last_err}")
    return None


def parse_titles(html: str) -> set[str]:
    """Extract floorplan titles via regex on the <a title="Floorplan X"> attr.

    BeautifulSoup doesn't reliably parse the <template>-wrapped chunks this
    endpoint returns, so we go straight to a regex on the attribute that's
    invariant across the cards.
    """
    (DEBUG_DIR / "response.html").write_text(html)
    print(f"[parse] markers: "
          f"<chunks>={html.count('<chunks>')} "
          f"<template={html.count('<template')} "
          f"jd-fp-floorplan-card={html.count('jd-fp-floorplan-card')} "
          f"<!DOCTYPE={html.count('<!DOCTYPE')}")
    titles = set(re.findall(r'<a[^>]+title="Floorplan ([^"]+)"', html))
    print(f"[parse] {len(titles)} titles: {sorted(titles)}")
    return titles


def emit_output(**kwargs) -> None:
    """Write key=value pairs to $GITHUB_OUTPUT for downstream workflow steps."""
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if not gh_out:
        return
    with open(gh_out, "a") as f:
        for k, v in kwargs.items():
            f.write(f"{k}={v}\n")


def main() -> int:
    # Heartbeat first so it writes even if everything else fails.
    write_heartbeat()

    r = fetch()
    if r is None:
        # Network unreachable: not a real failure, just a missed beat.
        print("[main] skipping this run; will retry on next schedule")
        emit_output(hit="false", matches="", all_matching="", available_count=0)
        return 0

    titles = parse_titles(r.text)

    print(f"\n[match] watch list:        {WATCH}")
    currently_matching = {w for w in WATCH if w in titles}
    print(f"[match] currently matching: {sorted(currently_matching)}")

    prev = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    prev_matching = set(prev.get("matching", []))

    if FORCE_ALERT:
        newly_available = currently_matching
        print("[match] FORCE_ALERT=true — bypassing state check")
    else:
        newly_available = currently_matching - prev_matching
    print(f"[match] newly available:    {sorted(newly_available)}")

    STATE_FILE.write_text(json.dumps({
        "all_available": sorted(titles),
        "matching": sorted(currently_matching),
    }, indent=2))

    emit_output(
        hit="true" if newly_available else "false",
        matches=", ".join(sorted(newly_available)),
        all_matching=", ".join(sorted(currently_matching)),
        available_count=len(titles),
    )

    if not titles:
        print("[warn] zero floorplan titles parsed — site may have changed. "
              "See debug/response.html in the workflow artifacts.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
