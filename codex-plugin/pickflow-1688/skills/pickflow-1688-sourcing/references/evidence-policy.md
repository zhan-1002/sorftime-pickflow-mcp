# Evidence and verdict policy

## Precedence

Use evidence in this order:

1. Structured hard mismatches from Pickflow.
2. Browser-verified SKU and listing facts.
3. Observable visual evidence.
4. Title and keyword semantics.

Lower-precedence evidence cannot override a higher-precedence mismatch. Missing data remains unknown.

## Verdicts

- `different`: at least one reliable critical mismatch exists.
- `likely_same`: critical identity fields are explicitly compatible, visual evidence agrees, and browser verification resolves SKU/pack ambiguity.
- `possible_same`: the product is visually and semantically compatible, but at least one critical fact still needs verification.
- `insufficient_evidence`: available evidence cannot distinguish identity reliably.

Never describe `likely_same` as a guaranteed exact match.

## Browser checklist

Verify and record only visible listing facts:

- selected SKU or variation;
- product form and included components;
- material;
- item dimensions versus package dimensions;
- sellable count, pack quantity, and unit;
- MOQ and price-tier threshold;
- whether the displayed price belongs to the selected SKU and pack;
- supplier/store name and relevant service signals.

Mark each field match, mismatch, or unknown and cite its source as `1688_browser`.

## Security verification recovery

Treat a visible security slider, CAPTCHA, or risk-control page as `verification_required` in the Codex review state. This is an interaction block, not evidence about the product and not an empty API result.

- Stop automated retries immediately; repeated navigation can trigger stricter risk controls.
- Do not bypass, script, or silently solve the challenge.
- Keep the current candidate and browser tab as a handoff.
- Ask the user to complete the verification in the same sidebar browser and tell you when it is ready. If the user asks Codex to solve a CAPTCHA, obtain explicit confirmation for that specific challenge before interacting with it.
- After the user confirms, inspect fresh visible page state in the same tab. Do not rerun Sorftime discovery unless the candidate page itself has expired.
- Keep SKU, pack, price, and browser-only fields unknown while verification is unresolved.

## Price comparability

Use `exact_pack_and_tier` only when the supplier price already represents one Amazon-comparable sellable unit. Use `normalized_from_pack` only when the browser establishes an explicit pack basis and the unit conversion is shown. Otherwise keep `ambiguous_sku_or_pack` or `unavailable` and withhold profit.
