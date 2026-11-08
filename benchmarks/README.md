# Detector benchmark

Reproducible comparison of **plain Playwright** and **shadowscrape Stealthy** against public bot-detector pages.

```bash
.venv/bin/python -m playwright install chromium          # once
.venv/bin/python -m benchmarks.detectors                  # every engine x every target, 1 run
.venv/bin/python -m benchmarks.detectors --repeat 5 --engines playwright stealthy
.venv/bin/python -m benchmarks.detectors --targets local-probe          # fully offline
.venv/bin/python -m benchmarks.detectors --headful --targets sannysoft creepjs   # real (visible) window
.venv/bin/python -m benchmarks.detectors --list
```

Outputs go to `benchmarks/results/<UTC stamp>.{json,md}` and are copied to `latest.json` / `latest.md`.
The stealth fingerprint is seeded (`--seed`, run *i* uses `seed + i`), so reruns are comparable.
With no network, public targets are recorded as `skipped` (the run still exits 0 and writes a report);
the `local-probe` target (a page served from this process that checks ~12 headless tells, including
worker/iframe consistency) always runs.

## Targets

| name | url | verdict rule (heuristic) |
|---|---|---|
| local-probe | served locally | no failed local checks |
| sannysoft | bot.sannysoft.com | zero `failed` result cells |
| creepjs | abrahamjuliot.github.io/creepjs | headless% = 0, stealth% = 0, no `webDriverIsOn` / `hasToStringProxy` / `hasBadWebGL` (like-headless% is reported, not judged) |
| browserscan | browserscan.net/bot-detection | `Test Results: Normal` |
| areyouheadless | arh.antoinevastel.com/bots/areyouheadless | "You are not Chrome headless" |
| tls-peet | tls.peet.ws/api/all | informational: JA3/JA4/Akamai hashes, UA |
| nowsecure | nowsecure.nl | no Cloudflare challenge markers and page rendered |

## Reference run (measured, not a promise)

macOS arm64, Python 3.9, playwright 1.60, patchright 1.60.1, Chromium 148, home network, headless, 3 runs, host-OS
default profile.

| target | plain Playwright | shadowscrape Stealthy |
|---|---|---|
| local-probe | fail 0/3 | pass 3/3 |
| sannysoft | fail 0/3 (12 failed rows) | pass 3/3 |
| creepjs | fail 0/3 (headless 100%) | pass 3/3 (headless 0%, stealth 0%, like-headless 31%) |
| browserscan | fail 0/3 (`Robot`) | pass 3/3 (`Normal`) |
| areyouheadless | unknown (site returned 502) | unknown (502) |
| tls-peet | info | info (identical JA3/JA4/Akamai hashes: Chromium's real TLS is unchanged) |
| nowsecure | pass 3/3 | pass 3/3 (does not discriminate from here: plain Playwright also passes) |

## Pages built for scraper testing (live Cloudflare)

`python -m benchmarks.permitted_targets` loads pages whose owners publish them for testing scrapers. Only add targets that invite testing; do not point it at production sites you don't own.

Nov 2026, macOS arm64, headless, home IP, **one run each**:

| page | plain Playwright | shadowscrape Stealthy |
|---|---|---|
| scrapingcourse.com/cloudflare-challenge | stuck on "Just a moment..." | passed ("You bypassed the Cloudflare challenge!") |
| nopecha.com/demo/cloudflare | blocked | blocked ("Sorry, you have been blocked") |
| bot.incolumitas.com | loads | loads (scores not read) |

The first row is the only live Cloudflare challenge we have beaten, and it is a practice page. The second row is a real failure: a hard block page that appears before any challenge, which points at IP or a stricter rule rather than the fingerprint. One run each, from one IP, says little about reliability.

## How to read this honestly

* A `pass` means *that page did not flag us*. It is **not** evidence of bypassing Cloudflare, Akamai, DataDome,
  PerimeterX/HUMAN or Kasada. They add IP reputation, TLS/HTTP2 fingerprints, JS challenge telemetry, behavioural
  and cross-request ML scoring that these pages do not exercise. No such vendor was measured.
* The parsers scrape third-party pages; they will rot. Check `details` in the JSON when a cell looks odd
  (`unknown` = the parser found nothing, e.g. a 502 or a layout change).
* Results depend on your IP, GPU and OS. Run it on the machine and network you will scrape from.
* New headless Chromium keeps a few tells that JS cannot patch (e.g. the `ActiveText` system colour, which feeds
  CreepJS's "like headless" rating). `--headful` (Linux: under Xvfb) is the strongest mode.
* Browser engines all present Chromium's real TLS/HTTP2 fingerprint. For non-browser requests use the
  curl_cffi-based `Fetcher`, which impersonates browsers at the TLS layer.
