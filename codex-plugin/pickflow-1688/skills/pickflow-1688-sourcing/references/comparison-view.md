# Candidate comparison view

Show only the information needed to choose the next verification action.

## Default layout

Use one row per candidate with:

- reference and candidate images;
- verdict and deterministic score;
- match, mismatch, and unknown evidence counts;
- MOQ and price comparability;
- one concise next action, such as “verify 24-piece SKU”;
- a link to open the 1688 detail page.

Sort hard-rejected candidates last or omit them from the active shortlist. Keep no more than five active candidates.

## Interactive behavior

When the visualization capability is available, create a compact theme-aware comparison that:

- selects one candidate at a time;
- exposes evidence details without hiding unknowns;
- distinguishes item measurements from package measurements;
- never computes profit while price comparability is ambiguous;
- offers a clearly labeled follow-up action for deeper browser verification.

If a security slider blocks a candidate, show `等待人工验证` as an interaction state. Do not display it as `different`, `unavailable`, or a product-data error, and keep the candidate selectable so review can resume from the preserved tab.

Do not embed credentials, raw API responses, private ASIN lists, or full-resolution external images in the visualization source. Use the returned URLs only as visible links or browser targets.
