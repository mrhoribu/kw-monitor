#!/usr/bin/env python3
"""
Kennedy-Warren floorplan availability monitor.

Polls the floorplans page for currently-available floorplans,
matches against a watch list, and emits GitHub Actions outputs that fire
notification steps on state transitions (not-available → available).

Behavior:
  - Retries network errors and the host's "One moment, please..." page
    up to 4 times with progressive backoff (15s, 30s, 45s).
  - Exits 0 on persistent network/HTTP/interstitial errors so transient
    failures don't pollute the workflow run history or fire failure
    notifications.
  - Exits 1 (without touching state) if the page no longer contains
    floorplan cards, so a site change fails loudly instead of silently.
  - Saves full response to debug/response.html for post-hoc inspection.
  - State tracking via state.json — only alerts on transitions.

Environment variables:
  WATCH_LIST  : comma-separated floorplan names, e.g. "Historic 03,Historic 05"
  FORCE_ALERT : "1"/"true" to bypass state check (useful for testing)
"""
import json, os, pathlib, re, time
import urllib.error, urllib.request

URL = "https://kennedywarren.com/floorplans/"

HEADERS = {
    "accept": "text/html",
    "accept-language": "en-US,en;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

WATCH = [w.strip() for w in os.environ.get("WATCH_LIST", "Historic 03").split(",")]
FORCE_ALERT = os.environ.get("FORCE_ALERT", "").lower() in ("1", "true", "yes")
STATE_FILE = pathlib.Path("state.json")
DEBUG_DIR = pathlib.Path("debug")
DEBUG_DIR.mkdir(exist_ok=True)


# Host sometimes serves a 200 "One moment, please..." page that reloads itself
# after 5s instead of the real page. Retry it like a network error.
INTERSTITIAL = "<title>One moment, please...</title>"


def fetch(max_attempts: int = 4) -> str | None:
    """Fetch the page with retries. Returns HTML or None on failure."""
    print(f"\n[fetch] {URL}")
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            req = urllib.request.Request(URL, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
                print(f"[fetch] HTTP {r.status}, {len(body)} bytes, "
                      f"content-type={r.headers.get('content-type')}")
                html = body.decode(r.headers.get_content_charset() or "utf-8",
                                   "replace")
            (DEBUG_DIR / "response.html").write_text(html)
            if INTERSTITIAL not in html:
                return html
            last_err = "interstitial page"
        except (urllib.error.URLError, TimeoutError) as e:  # HTTPError is a URLError
            last_err = f"{type(e).__name__}: {e}"
        wait = 15 * attempt   # 15s, 30s, 45s
        print(f"[fetch] attempt {attempt}/{max_attempts} failed: {last_err}")
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
    html = fetch()
    if html is None:
        # Network unreachable or stuck on the interstitial: not a real
        # failure, just a missed beat.
        print("[main] skipping this run; will retry on next schedule")
        emit_output(hit="false", matches="", all_matching="", available_count=0)
        return 0

    titles = parse_titles(html)
    if "jd-fp-floorplan-card" not in html:
        # Page shape changed (wrong endpoint, redesign, bot wall). Don't
        # overwrite state with an empty list -- that would re-alert on
        # everything once parsing works again. Fail so the alert step fires.
        print("[error] no floorplan cards in response -- site may have changed. "
              "See debug/response.html in the workflow artifacts.")
        return 1

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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
