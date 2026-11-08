# Security policy

## Reporting a vulnerability

Please report security issues privately through GitHub's "Report a vulnerability" button on the Security tab of this repository, not in a public issue. Include the version, steps to reproduce and the impact. Expect a first reply within about a week.

## Scope

In scope: code execution or data exposure through the library, CLI, MCP server or shell (for example unsafe handling of fetched content, path traversal in exporters, or the MCP server exposing local files).

Out of scope: a website detecting or blocking the scraper. That is a stealth-quality issue, so open a normal issue.

## Using the MCP server safely

The MCP server fetches arbitrary URLs for an AI agent. Run it only with agents you trust, and remember that fetched pages can contain instructions aimed at the agent. Treat tool output as data.
