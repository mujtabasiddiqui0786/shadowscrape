# Fetchers cheat sheet

All return `Response` (a `Selector` subclass). Common options: `headers, cookies, proxy, timeout, retries, retry_delay, impersonate, follow_redirects, params, data, json, stealthy_headers=True`.

## HTTP (curl_cffi, TLS + HTTP/2 impersonation)
```python
from shadowscrape import Fetcher, AsyncFetcher, FetcherSession
Fetcher.get(url, impersonate="chrome", proxy="http://u:p@host:8080")
Fetcher.post(url, json={"a": 1})          # also put, delete
await AsyncFetcher.get(url)
with FetcherSession(impersonate="chrome") as s:   # persistent cookies
    s.get(login); s.get(data)
```
`timeout` is in seconds.

## Browser (Playwright)
```python
from shadowscrape import DynamicFetcher
DynamicFetcher.fetch(url, headless=True, wait_selector=".item", wait=1, network_idle=True,
                     page_action=lambda page: page.click("#more"), capture_xhr=r"/api/", proxy=..., disable_resources=True)
```
`page.captured_xhr` is a list of `{url, method, status, headers, body}`. Sessions: `DynamicSession`, `AsyncDynamicSession` keep one browser open across fetches.

## Stealth browser
```python
from shadowscrape import StealthyFetcher
StealthyFetcher.fetch(url, solve_cloudflare=True, humanize=True, block_webrtc=True, fingerprint_profile=...)
```
Same API as DynamicFetcher plus evasions (webdriver flag, WebGL/canvas noise, consistent client hints, CDP leak mitigation). Sessions: `StealthySession`, `AsyncStealthySession`. No third-party CAPTCHA services are used; Turnstile is solved by real-behavior interaction.

## Proxies
`ProxyRotator(["http://p1", "http://p2"], strategy="cyclic"|"random"|"least_used")`, pass as `proxy_rotator=`.

## Troubleshooting
- 403/429: switch to `impersonate="chrome"`, then Stealthy, add a proxy, slow down.
- Empty body but status 200: content is JS-rendered; use DynamicFetcher with `wait_selector`.
- `playwright` errors about missing browser: run `shadowscrape install`.
