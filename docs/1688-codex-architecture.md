# Codex-native 1688 supplier matching

## Goal

Turn one Amazon ASIN into an evidence-backed shortlist of 1688 supplier candidates without treating a similar title, image, or displayed price as proof of an exact match.

The first vertical slice is deliberately small: one ASIN, at most five reviewed candidates, explicit unknown states, and no unit-cost or profit output when SKU, pack quantity, or price tier is ambiguous.

## Responsibility boundary

The implementation is split at a stable JSON contract in `supplier_contracts.py`.

### MCP backend

The backend is deterministic and testable. It:

1. Normalizes the full Sorftime `product_detail` response into a `ProductFingerprint`.
2. Queries Sorftime's 1688 image and keyword search endpoints.
3. Deduplicates and normalizes supplier candidates.
4. Applies hard mismatch gates and records structured evidence.
5. Builds a `VisualReviewBundle` containing only the fields and image URLs needed by Codex.

The backend does not invoke an LLM, control a browser, render a UI, or expose raw API payloads through the stable contract.

### Codex orchestration layer

A later Codex plugin/skill will:

1. Ask native vision to compare the Amazon reference images with candidate images.
2. Record short, structured `MatchEvidence`; model chain-of-thought is never stored.
3. Open promising candidates in the signed-in sidebar browser to verify SKU, MOQ, pack quantity, dimensions, material, and price tiers.
4. Present an interactive comparison and FBA estimate only after price comparability is established.

This layer replaces the old Kimi WebBridge dependency. The plugin scaffold is intentionally deferred until the backend contracts and first vertical slice are accepted.

## Data flow

```text
ASIN
  -> Sorftime product_detail
  -> ProductFingerprint
  -> image search + Chinese keyword search
  -> SupplierCandidate[]
  -> deterministic hard gates
  -> SupplierComparison[] + VisualReviewBundle
  -> Codex native vision review
  -> sidebar browser verification
  -> comparable unit cost
  -> interactive shortlist / FBA estimate
```

The Amazon-side fingerprint is the reference truth. Previous calls discarded useful product identity fields, so normalization must preserve their meaning even though raw responses remain internal.

## Evidence policy

Evidence precedence is:

1. Explicit structured mismatch, such as incompatible dimensions, material, count, or product type.
2. Browser-verified SKU and listing facts.
3. Visual similarity and visible feature comparison.
4. Title and keyword semantics.

Rules:

- A hard mismatch forces the `different` verdict.
- Vision can add evidence but cannot override a structured hard mismatch.
- An unknown critical field is not a match and requires browser review.
- A visually similar candidate remains `possible_same` until critical structured facts are verified.
- The final UI must show match, mismatch, and unknown evidence separately.

## Stable contracts

`src/pickflow_mcp_server/supplier_contracts.py` defines version `1.0` models for:

- Amazon product fingerprints and source references
- supplier candidates and price tiers
- per-dimension evidence and comparison verdicts
- visual review bundles
- lookup results and API call counts

Raw payloads and undeclared fields are rejected at this boundary. Contract changes require a separate review because both the backend and Codex layer depend on them.

## Proposed MCP tools

The backend vertical slice exposes three tools:

- `asin_fingerprint`: build a normalized fingerprint and report critical unknowns.
- `supplier_search`: run authorized 1688 candidate searches and return normalized, deduplicated candidates.
- `supplier_compare_prepare`: apply deterministic gates and produce comparisons plus a visual review bundle.

The Codex skill orchestrates these tools rather than duplicating endpoint knowledge.

## Price and profit safety

1688's lowest displayed price is not automatically a comparable unit cost. A normalized price may be published only when:

- the selected SKU is known;
- pack quantity and unit are known;
- the relevant quantity tier is known; and
- currency and normalization basis are recorded.

Otherwise `price_comparability` is `ambiguous_sku_or_pack` or `unavailable`, and FBA profit calculation is withheld.

## Privacy and quota controls

- Credentials, raw API responses, private ASIN lists, screenshots, and user test data stay outside Git.
- Tests use synthetic fixtures only.
- Live probes must report API call counts and use an explicit small budget.
- Logs and committed examples contain aggregate field coverage, not product identifiers or raw response bodies.
- External image URLs are treated as untrusted input and are not downloaded by the backend unless explicitly required.

## First-slice acceptance

For one authorized ASIN, the system must be able to produce a fingerprint, search and deduplicate candidates, reject deterministic mismatches, prepare no more than five candidates for visual review, and clearly identify which facts still need browser verification. It must not claim an exact match or calculate profit from an ambiguous supplier price.
