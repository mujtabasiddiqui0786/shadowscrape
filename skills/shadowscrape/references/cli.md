# CLI and shell cheat sheet

```bash
shadowscrape get|post|fetch|stealthy-fetch <url> <outfile.(html|md|txt|json)> [options]
```
| Flag | Meaning |
|---|---|
| `--css SEL` | keep only matching elements (supports `::text`, `::attr(x)`) |
| `--main-content-only` | strip nav/footer (markdown) |
| `--proxy URL` | proxy |
| `-H 'K: V'` / `--headers` | header, repeatable (or JSON object) |
| `--cookies 'a=b; c=d'` | cookies (or JSON) |
| `--timeout SEC` | default 30 |
| `--impersonate chrome` | get/post only |
| `--data`, `--json`, `--params k=v` | post body / query |
| `--wait-selector SEL`, `--wait SEC`, `--network-idle`, `--no-headless` | fetch / stealthy-fetch |
| `--solve-cloudflare` | stealthy-fetch only |

Exit codes: 0 ok, 1 error, 2 HTTP status >= 400 (file is still written).

Other commands:
- `shadowscrape extract saved.html out.md --css article` (local file or `-` for stdin; no network)
- `shadowscrape install [--with-deps]` installs Playwright Chromium
- `shadowscrape mcp [--http]`
- `shadowscrape shell [-c "code"]`

## Shell
Preloaded: `Fetcher AsyncFetcher DynamicFetcher StealthyFetcher Selector`, `page` (last response), `view(page)` opens in browser, `curl2fetcher("curl ...")` runs a copied curl command (headers, cookies, data, method, basic auth, proxy) through `Fetcher` and sets `page`; `curl2fetcher(cmd, execute=False)` returns the parsed request dict. Uses IPython when installed.
