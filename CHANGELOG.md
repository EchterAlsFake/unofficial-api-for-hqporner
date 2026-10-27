# Changelog

## 2.5.2 — 2026-10-27

- Fetch distinct listing pages and handle the all-time sorting option correctly.
- Return an empty search when the site shows recommendations below its no-results message.
- Accept canonical, www and mobile actress links, and correct the mobile 404 fallback.
- Limit category results to actual category cards and resolve listing and image links correctly.
- Extract CDN download links from both ordinary anchors and JavaScript HTML strings.
- Reject zero page or concurrency limits and report CLI batch failures accurately.
- Use the published `eaf-base-api>=4.2.5` dependency.

