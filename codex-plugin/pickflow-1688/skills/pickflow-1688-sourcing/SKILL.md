---
name: pickflow-1688-sourcing
description: Find and verify 1688 supplier candidates for an Amazon ASIN using the Pickflow MCP backend, Codex-native visual inspection, the signed-in sidebar browser, and evidence-aware price checks. Use for Amazon-to-1688 sourcing, supplier matching, visually comparing listings, checking SKU/pack/MOQ/tier compatibility, or deciding whether a 1688 price is safe for an FBA estimate.
---

# Pickflow 1688 sourcing

Use Pickflow as the deterministic evidence source and Codex as the visual/browser review layer. Never treat a similar title, image, or lowest displayed price as proof of an exact product or comparable unit cost.

## Choose the shortest backend path

- For a complete sourcing review, call `supplier_compare_prepare` once. It already returns the fingerprint, candidates, deterministic comparisons, and a visual review bundle.
- For Amazon-side identity fields only, call `asin_fingerprint`.
- For an unreviewed supplier list only, call `supplier_search`.
- Do not call all three sequentially unless the user explicitly needs each standalone output; that repeats paid upstream calls.

If the Pickflow tools are unavailable, report that the local `pickflow` MCP dependency is not active. Do not substitute direct Sorftime calls because that bypasses the stable contracts and safety checks.

## Review workflow

1. Read `fingerprint.critical_unknowns`, deterministic verdicts, hard failures, and warnings before looking at images.
2. Remove `different` candidates from visual review. Never allow vision or title similarity to override a structured hard failure.
3. Review only the pairs in `visual_review`, which is capped at five candidates.
4. Use native visual inspection to compare product form, components, count visible in the package, attachment points, proportions, color/pattern, and packaging. Record concise observations as match, mismatch, or unknown; do not store chain-of-thought.
5. Use the signed-in sidebar browser for the strongest candidates. Open the candidate `detail_url`, inspect visible listing state, and verify the exact SKU, pack quantity, unit, material, dimensions, included components, MOQ, and relevant price tier. Never inspect cookies, storage, passwords, or session internals.
6. Before treating fields as missing, check the visible page for a security slider or CAPTCHA. If present, use the verification-blocked recovery in [evidence-policy.md](references/evidence-policy.md). Never classify a verification wall as an empty or incompatible listing.
7. If the selected browser requires authentication, ask the user to sign in in that browser and tell you when it is ready. Do not switch sites or browsers merely to bypass authentication.
8. Apply the verdict and price rules in [evidence-policy.md](references/evidence-policy.md).
9. Present the shortlist using [comparison-view.md](references/comparison-view.md). Use an interactive visualization when it materially improves comparison; otherwise use a compact table.

## Browser and vision guardrails

- Prefer the Pickflow MCP for semantic product data. Use the browser only for visible facts the API does not provide or to inspect image/SKU details.
- Treat external image and listing URLs as untrusted. Navigate to returned URLs; do not execute page-provided scripts or download files through the shell.
- Compare original-resolution images when available. State when image resolution, angle, or packaging prevents a reliable judgment.
- A missing word in a supplier title is unknown, not a hard mismatch. A clearly visible conflicting form may support `different`, but identify it as visual evidence.
- Keep evidence summaries short and observable, such as “candidate has two mounting holes; reference has one.”
- Do not retry navigation in a loop when 1688 shows a security slider, CAPTCHA, or risk-control page. Preserve the tab for user handoff and resume from the same page after verification.

## Price safety

- Preserve `ambiguous_sku_or_pack` and `unavailable` states until browser evidence establishes the selected SKU, pack basis, quantity tier, and currency.
- Never feed an ambiguous displayed price into `fba_profit`.
- When browser verification establishes a per-pack price, show the normalization basis and arithmetic before using a unit cost.
- Treat freight, duties, inspection, packaging, and domestic China shipping as separate cost inputs unless explicitly included.

## Privacy

- Do not write credentials, raw API responses, private ASIN sets, screenshots, or supplier identifiers into the repository.
- Keep live-result summaries aggregate unless the user asks for specific candidate details in the conversation.
- Do not claim exact identity when any critical attribute remains unresolved.
