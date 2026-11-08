# Contributing to shadowscrape

Thanks for helping. Bug reports, benchmark runs on other machines, docs fixes and new features are all welcome.

## Set up

```bash
git clone https://github.com/mujtabasiddiqui0786/shadowscrape.git
cd shadowscrape
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[stealth]" pytest
python -m playwright install chromium
```

## Run the tests

```bash
python -m pytest                      # everything; browser tests need chromium
python -m pytest -m "not browser"     # offline, no browser
```

Unit tests must run offline: use a local `http.server` fixture or HTML strings, never a live site.

## Layout

| Path | What it holds |
|---|---|
| `shadowscrape/parser/` | `Selector`, adaptive tracking, Markdown output |
| `shadowscrape/fetchers/` | HTTP fetchers and the stealth browser fetchers |
| `shadowscrape/stealth/` | fingerprint profiles, JS evasions, humanized input, Turnstile handling |
| `shadowscrape/spiders/` | spiders, scheduler, AutoThrottle, exporters, templates |
| `shadowscrape/cli.py`, `shell.py`, `mcp_server.py` | CLI, shell, MCP server |
| `benchmarks/` | reproducible detector benchmark |

## Pull requests

- Keep a change focused, and add or update tests.
- Match the surrounding code: type hints, `from __future__ import annotations`, docstrings on public API.
- Python 3.9 must keep working.
- Don't claim a bypass you haven't measured. Stealth changes should come with a benchmark run or a test.
- Describe how you checked the change.

## Benchmark contributions

Run `python -m benchmarks.detectors --repeat 3` on your machine and open an issue with the generated `benchmarks/results/latest.md`, plus your OS, GPU and network type. Results from other machines are the most useful data we can get.

## Responsible use

Don't submit features whose main purpose is abuse (credential stuffing, attacking sites, evading bans after being asked to stop). Scraping tools are dual-use, and we keep this project aimed at legitimate data collection.
