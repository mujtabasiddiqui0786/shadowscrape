# Stealth

`StealthyFetcher` and `StealthySession` use internally consistent fingerprint profiles (user agent, client hints, platform, screen, WebGL, languages, timezone), JS evasions that keep patched functions looking native, WebRTC blocking, humanized mouse and typing, and Cloudflare Turnstile handling by real interaction with the challenge frame. No third-party solving services are used.

## What is measured

The detector benchmark (`python -m benchmarks.detectors`) runs plain Playwright and shadowscrape against public detector pages. In the reference run, plain Playwright fails sannysoft, CreepJS and BrowserScan while shadowscrape passes them. See `benchmarks/README.md` for the full table, the method and how to rerun it on your own machine.

## Live Cloudflare practice pages

On ScrapingCourse's Cloudflare challenge page (published for scraper testing), plain Playwright stayed stuck on the interstitial and shadowscrape passed. On NopeCHA's Cloudflare demo both were hard-blocked. These were single runs from one IP, so treat them as a data point, not a reliability figure. Reproduce with `python -m benchmarks.permitted_targets`.

## What is not proven

- Public detector pages test fingerprint consistency only. They do not test IP reputation, behavioural or ML scoring, or vendor JS challenges.
- No production Cloudflare, Akamai, DataDome, PerimeterX or Kasada deployment has been tested, and the Turnstile solver is verified only against a local mock.
- Proxies and IP reputation matter at least as much as the fingerprint.
- For non-browser requests use `Fetcher`, because a browser's own TLS fingerprint is left untouched.
- `noise=True` (canvas/audio noise) can be flagged by strict lie detectors. Set `noise=False` if that matters.
- Headful mode (under Xvfb on Linux) is stronger than headless.
