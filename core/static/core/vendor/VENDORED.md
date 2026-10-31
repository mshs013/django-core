# Vendored front-end assets

| Library | Version (from file header) | License | Notes |
|---|---|---|---|
| Tabler (CSS/JS) | 1.7.1 | MIT (`tabler/LICENSE-MIT.txt`) | header cites MIT; from the npm package `@tabler/core@1.7.1` (`dist/`) |
| Tabler Icons (webfont CSS) | 3.48.0 | MIT (`icons/LICENSE-MIT.txt`) | header cites upstream LICENSE |
| HTMX | 2.0.11 (string found in the minified file) | 0BSD (`htmx/LICENSE-0BSD.txt`) | |
| Alpine.js | 3.17.4 (string found in the minified file) | MIT (`alpine/LICENSE-MIT.txt`) | |
| Noto Sans (latin, bengali 400/700) | as shipped | SIL OFL 1.1 (`fonts/LICENSE-OFL.txt`) | |
| Chart.js (UMD build) | 4.4.1 | MIT (`chartjs/LICENSE-MIT.txt`) | downloaded from the npm package via jsDelivr; sha256 `74401d738dd3e03ee5dfb3b6841210fe2c4ead8a960c4011ca4ba0b78a9fd8f3`. Report charts (`core/js/charts.js`). |

All assets are served from the app's own static files: no CDN at runtime. To update one, replace its files from the npm
package named above, update this table, run `collectstatic` and check the pages in a browser.
