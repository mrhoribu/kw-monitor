# kw-monitor

Watches the [Kennedy-Warren floorplans page](https://kennedywarren.com/floorplans/) for specific
floorplans becoming available, and sends a Discord message and an email when one shows up.

## How it works

1. A GitHub Actions workflow ([`kw-monitor.yml`](.github/workflows/kw-monitor.yml)) runs
   [`monitor.py`](monitor.py). It currently has only a `workflow_dispatch` trigger, so something
   outside the repo has to start each run. Uncomment the `schedule:` block to run it on GitHub's cron.
2. `monitor.py` downloads the floorplans page and collects every listed floorplan name
   (e.g. `Historic 30`, `South 10A`), then checks them against `WATCH_LIST`.
3. The results are compared with the last run's results in [`state.json`](state.json). An alert goes
   out only when a watched floorplan **newly** appears, so it doesn't repeat every run.
4. The workflow sends the alert to Discord and/or by email through [Resend](https://resend.com),
   then commits the updated `state.json` back to the repo.

The script uses only the Python standard library. Nothing is installed with pip.

## Configuration

| Where | Name | Purpose |
|---|---|---|
| workflow `env` | `WATCH_LIST` | Comma-separated floorplan names to watch, e.g. `Historic 01,Historic 14` |
| env (optional) | `FORCE_ALERT` | `1`/`true` sends an alert for every current match, ignoring `state.json` (for testing) |
| repo secret | `DISCORD_WEBHOOK_URL` | Discord webhook for alerts and failure notices |
| repo secret | `RESEND_API_KEY` | Resend API key for email alerts |
| repo secret | `RESEND_FROM` | Sender address (must be on a domain verified in Resend) |
| repo secret | `RESEND_TO` | Comma-separated recipient addresses |

## Failure behavior

- **Network or HTTP errors** are retried 3 times. If they keep failing, the run is skipped quietly and
  `state.json` is left alone.
- **The page loads but has no floorplan cards** (the site changed, or the bot is being blocked):
  the run fails, `state.json` is left alone, and a failure notice goes to Discord. The page that came
  back is saved as the `debug-response` workflow artifact (kept for 1 day) so you can inspect it.

## Running locally

```bash
WATCH_LIST="Historic 30" python3 monitor.py
```

This writes `state.json` and `debug/response.html` in the current directory.
