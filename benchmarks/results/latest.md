# Detector benchmark

Generated 2026-11-07T07:00:33Z | python 3.9.6 | Darwin 25.6.0 arm64 | playwright 1.60.0 | patchright 1.60.1

Network available: **True** | runs per cell: 3 | headless: True

| target | plain Playwright | shadowscrape Stealthy |
|---|---|---|
| local-probe | fail 0/3 | pass 3/3 |
| sannysoft | fail 0/3 | pass 3/3 |
| creepjs | fail 0/3 | pass 3/3 |
| browserscan | fail 0/3 | pass 3/3 |
| areyouheadless | unknown 0/3 | unknown 0/3 |
| tls-peet | info | info |
| nowsecure | pass 3/3 | pass 3/3 |

`pass n/m` = detector did not flag the client in n of m runs. `info` = informational target (fingerprints below).

## Details

- **playwright / local-probe / run 0**: fail (1.1s) `{"fails": ["navigator.webdriver", "headless-ua", "plugins", "window.chrome", "uach-headless", "outer==inner", "permissions", "rtt0", "swiftshader"], "checks": 12}`
- **playwright / local-probe / run 1**: fail (0.8s) `{"fails": ["navigator.webdriver", "headless-ua", "plugins", "window.chrome", "uach-headless", "outer==inner", "permissions", "rtt0", "swiftshader"], "checks": 12}`
- **playwright / local-probe / run 2**: fail (0.8s) `{"fails": ["navigator.webdriver", "headless-ua", "plugins", "window.chrome", "uach-headless", "outer==inner", "permissions", "rtt0", "swiftshader"], "checks": 12}`
- **playwright / sannysoft / run 0**: fail (7.0s) `{"passed": 18, "failed": 12, "warn": 1, "failed_rows": ["User Agent (Old) Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/1", "WebDriver (New) present (failed)", "Chrome (New) missing (failed)", "Permissions (New) prompt", "Plugins Length (Old) 0", "Plugins is of type PluginArray failed", "WebGL Renderer ANGLE (Google, Vulkan 1.3.0 (SwiftShader`
- **playwright / sannysoft / run 1**: fail (5.7s) `{"passed": 18, "failed": 12, "warn": 1, "failed_rows": ["User Agent (Old) Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/1", "WebDriver (New) present (failed)", "Chrome (New) missing (failed)", "Permissions (New) prompt", "Plugins Length (Old) 0", "Plugins is of type PluginArray failed", "WebGL Renderer ANGLE (Google, Vulkan 1.3.0 (SwiftShader`
- **playwright / sannysoft / run 2**: fail (5.1s) `{"passed": 18, "failed": 12, "warn": 1, "failed_rows": ["User Agent (Old) Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/1", "WebDriver (New) present (failed)", "Chrome (New) missing (failed)", "Permissions (New) prompt", "Plugins Length (Old) 0", "Plugins is of type PluginArray failed", "WebGL Renderer ANGLE (Google, Vulkan 1.3.0 (SwiftShader`
- **playwright / creepjs / run 0**: fail (14.1s) `{"headless_pct": 100, "like_headless_pct": 94, "stealth_pct": 0, "webdriver_is_on": true, "to_string_proxy": false, "bad_webgl": false}`
- **playwright / creepjs / run 1**: fail (13.5s) `{"headless_pct": 100, "like_headless_pct": 94, "stealth_pct": 0, "webdriver_is_on": true, "to_string_proxy": false, "bad_webgl": false}`
- **playwright / creepjs / run 2**: fail (13.2s) `{"headless_pct": 100, "like_headless_pct": 94, "stealth_pct": 0, "webdriver_is_on": true, "to_string_proxy": false, "bad_webgl": false}`
- **playwright / browserscan / run 0**: fail (8.8s) `{"test_results": "Robot"}`
- **playwright / browserscan / run 1**: fail (8.7s) `{"test_results": "Robot"}`
- **playwright / browserscan / run 2**: fail (8.7s) `{"test_results": "Robot"}`
- **playwright / areyouheadless / run 0**: unknown (4.2s) `{"snippet": "502 Bad Gateway\nnginx/1.18.0 (Ubuntu)"}`
- **playwright / areyouheadless / run 1**: unknown (3.5s) `{"snippet": "502 Bad Gateway\nnginx/1.18.0 (Ubuntu)"}`
- **playwright / areyouheadless / run 2**: unknown (3.5s) `{"snippet": "502 Bad Gateway\nnginx/1.18.0 (Ubuntu)"}`
- **playwright / tls-peet / run 0**: info (3.1s) `{"http_version": "h2", "ja3_hash": "fc061a05f1ac1f02645670f181e02e37", "ja4": "t13d2610h2_6d1bcf7a4624_188c7f576dcd", "akamai_hash": "506cb98be7fbfd29c286c57a6c570126", "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/148.0.7778.96 Safari/537.36"}`
- **playwright / tls-peet / run 1**: info (2.0s) `{"http_version": "h2", "ja3_hash": "fc061a05f1ac1f02645670f181e02e37", "ja4": "t13d2610h2_6d1bcf7a4624_188c7f576dcd", "akamai_hash": "506cb98be7fbfd29c286c57a6c570126", "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/148.0.7778.96 Safari/537.36"}`
- **playwright / tls-peet / run 2**: info (1.8s) `{"http_version": "h2", "ja3_hash": "fc061a05f1ac1f02645670f181e02e37", "ja4": "t13d2610h2_6d1bcf7a4624_188c7f576dcd", "akamai_hash": "506cb98be7fbfd29c286c57a6c570126", "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/148.0.7778.96 Safari/537.36"}`
- **playwright / nowsecure / run 0**: pass (11.4s) `{"note": "target content rendered, no challenge markers"}`
- **playwright / nowsecure / run 1**: pass (10.2s) `{"note": "target content rendered, no challenge markers"}`
- **playwright / nowsecure / run 2**: pass (10.9s) `{"note": "target content rendered, no challenge markers"}`
- **stealthy / local-probe / run 0**: pass (1.8s) `{"fails": [], "checks": 12}`
- **stealthy / local-probe / run 1**: pass (1.1s) `{"fails": [], "checks": 12}`
- **stealthy / local-probe / run 2**: pass (1.1s) `{"fails": [], "checks": 12}`
- **stealthy / sannysoft / run 0**: pass (7.8s) `{"passed": 31, "failed": 0, "warn": 0, "failed_rows": []}`
- **stealthy / sannysoft / run 1**: pass (7.5s) `{"passed": 31, "failed": 0, "warn": 0, "failed_rows": []}`
- **stealthy / sannysoft / run 2**: pass (7.4s) `{"passed": 31, "failed": 0, "warn": 0, "failed_rows": []}`
- **stealthy / creepjs / run 0**: pass (16.4s) `{"headless_pct": 0, "like_headless_pct": 31, "stealth_pct": 0, "webdriver_is_on": false, "to_string_proxy": false, "bad_webgl": false}`
- **stealthy / creepjs / run 1**: pass (15.0s) `{"headless_pct": 0, "like_headless_pct": 31, "stealth_pct": 0, "webdriver_is_on": false, "to_string_proxy": false, "bad_webgl": false}`
- **stealthy / creepjs / run 2**: pass (16.3s) `{"headless_pct": 0, "like_headless_pct": 31, "stealth_pct": 0, "webdriver_is_on": false, "to_string_proxy": false, "bad_webgl": false}`
- **stealthy / browserscan / run 0**: pass (11.0s) `{"test_results": "Normal"}`
- **stealthy / browserscan / run 1**: pass (10.3s) `{"test_results": "Normal"}`
- **stealthy / browserscan / run 2**: pass (11.2s) `{"test_results": "Normal"}`
- **stealthy / areyouheadless / run 0**: unknown (6.2s) `{"snippet": "502 Bad Gateway\n502 Bad Gateway\nnginx/1.18.0 (Ubuntu)"}`
- **stealthy / areyouheadless / run 1**: unknown (5.5s) `{"snippet": "502 Bad Gateway\n502 Bad Gateway\nnginx/1.18.0 (Ubuntu)"}`
- **stealthy / areyouheadless / run 2**: unknown (4.9s) `{"snippet": "502 Bad Gateway\n502 Bad Gateway\nnginx/1.18.0 (Ubuntu)"}`
- **stealthy / tls-peet / run 0**: info (4.0s) `{"http_version": "h2", "ja3_hash": "fc061a05f1ac1f02645670f181e02e37", "ja4": "t13d2610h2_6d1bcf7a4624_188c7f576dcd", "akamai_hash": "506cb98be7fbfd29c286c57a6c570126", "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"}`
- **stealthy / tls-peet / run 1**: info (3.7s) `{"http_version": "h2", "ja3_hash": "fc061a05f1ac1f02645670f181e02e37", "ja4": "t13d2610h2_6d1bcf7a4624_188c7f576dcd", "akamai_hash": "506cb98be7fbfd29c286c57a6c570126", "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"}`
- **stealthy / tls-peet / run 2**: info (3.5s) `{"http_version": "h2", "ja3_hash": "fc061a05f1ac1f02645670f181e02e37", "ja4": "t13d2610h2_6d1bcf7a4624_188c7f576dcd", "akamai_hash": "506cb98be7fbfd29c286c57a6c570126", "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"}`
- **stealthy / nowsecure / run 0**: pass (27.1s) `{"note": "target content rendered, no challenge markers"}`
- **stealthy / nowsecure / run 1**: pass (26.2s) `{"note": "target content rendered, no challenge markers"}`
- **stealthy / nowsecure / run 2**: pass (35.3s) `{"note": "target content rendered, no challenge markers"}`

## Caveats

- Parsers are heuristics over third-party pages and can break when those pages change; check `details`.
- Passing public detector pages is **not** evidence of bypassing Cloudflare/Akamai/DataDome/PerimeterX/Kasada; those also use IP reputation, TLS/HTTP2 fingerprints, and behavioural/ML scoring.
- Browser engines share Chromium's real TLS fingerprint (`tls-peet`); stealth patches do not alter it.
- Results depend on your IP and machine (GPU, fonts, OS). Run on the machine you intend to scrape from.
