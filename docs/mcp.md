# MCP server

Start: `shadowscrape mcp` (stdio). HTTP: `shadowscrape mcp --http --port 8000` (POST /mcp, JSON responses).
Implementation: built-in JSON-RPC 2.0 server (initialize, ping, tools/list, tools/call), newline-delimited over stdio.

Client config (Claude Desktop / Claude Code style):
```json
{"mcpServers": {"shadowscrape": {"command": "shadowscrape", "args": ["mcp"]}}}
```
Claude Code: `claude mcp add shadowscrape -- shadowscrape mcp`.

## Tools
| Tool | Use |
|---|---|
| `get` / `bulk_get` | HTTP with TLS impersonation. First choice. |
| `fetch` / `bulk_fetch` | Chromium rendering for JS pages. |
| `stealthy_fetch` / `bulk_stealthy_fetch` | Anti-bot evasion; `solve_cloudflare`. |

Shared args: `url` (or `urls`), `extraction_type` markdown|html|text (default markdown), `css_selector`, `main_content_only` (default true, ignored when css_selector set), `proxy`, `timeout` (seconds). Browser tools add `wait_selector`, `wait`, `network_idle`, `headless`, `disable_resources`. HTTP tools add `impersonate`, `headers`, `cookies`, `params`, `follow_redirects`, `retries`.

Result text is JSON `{url, status, content: [...]}`; bulk tools return a list, with `error` set on failed items. Failures come back as `isError: true` results.

## Saving tokens
Always set `css_selector` ("article", ".price::text", "a::attr(href)"): each match becomes one content string. Use `main_content_only` for articles. Prefer `text` for short facts and `markdown` for reading.
