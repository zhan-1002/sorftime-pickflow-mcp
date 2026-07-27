# Claude task packet: 1688 backend vertical slice

## Branch and ownership

Create `claude/1688-backend` from the accepted `agent/1688-codex-architecture` commit, preferably in a separate Git worktree.

Claude owns the deterministic Python backend and synthetic tests. Codex owns the contracts, later plugin/skill orchestration, native visual review, sidebar-browser workflow, visualization, and final integration review.

Do not modify `src/pickflow_mcp_server/supplier_contracts.py`. If a contract is insufficient, document the exact proposed change and stop at the adapter boundary so Codex can review it first.

## Required implementation

### 1. Product fingerprint extraction

Add a deterministic extractor for Sorftime `product_detail` responses.

- Map known aliases into `ProductFingerprint`.
- Extract identity, brand, model, product type, material, color, quantity/count, measurements, variation text, bullets, images, and source-field provenance where available.
- Populate `critical_unknowns` and a defensible completeness score.
- Tolerate missing and renamed fields without inventing values.
- Before coding the final alias map, use only a small authorized sample and record aggregate key/non-null coverage. Do not commit identifiers or raw responses.

### 2. Sorftime 1688 adapters

Add API client wrappers for the already verified Sorftime operations:

- `ali1688_similar_product`
- `ali1688_product_search_from_image`

Normalize their responses into `SupplierCandidate` and `PriceTier`. Handle empty data, non-JSON responses, partial rows, and endpoint errors without crashing the entire lookup.

### 3. Candidate normalization and deduplication

- Merge image-search and keyword-search candidates by stable product URL or supplier product ID.
- Preserve all search origins in `search_methods`.
- Normalize price tiers, MOQ, supplier identity, sales/order signals, images, and any structured attributes returned by the API.
- Never publish `normalized_unit_price` unless pack/SKU/tier comparability is exact or explicitly normalized.

### 4. Deterministic comparison

Implement a transparent prefilter that compares fingerprint and candidates by product type, count/pack, dimensions, material, variant, and other critical attributes.

- Record `MatchEvidence` with match, mismatch, or unknown status.
- Any critical hard mismatch must produce verdict `different`.
- Unknown must remain unknown; do not convert missing data into positive evidence.
- Title similarity and image availability may rank review order but cannot prove an exact match.
- Produce a `VisualReviewBundle` for at most the five best non-rejected candidates.
- Do not call an LLM or vision API.

### 5. MCP surface

Register three tools in `server.py`:

- `asin_fingerprint`
- `supplier_search`
- `supplier_compare_prepare`

Use the contract models as the stable serialization boundary. Include warnings, partial status, and API-call count in results. Keep raw endpoint payloads internal.

### 6. Tests and documentation

Add synthetic tests for:

- alias and missing-field fingerprint extraction;
- deduplication across both search methods;
- hard mismatch veto;
- unknown critical fields;
- price-tier ambiguity;
- empty and malformed endpoint results;
- server tool registration and serialization;
- no regression in existing tools.

Update README/TODO tool counts and usage documentation only after the new tools are registered.

## Out of scope

- Kimi WebBridge, Playwright, Selenium, or direct browser automation
- Codex plugin/skill scaffolding or UI code
- OpenAI or other LLM/vision API calls
- hardcoded credentials, private ASINs, raw test responses, or user datasets
- claims of exact identity based only on title or images
- profit estimates based on an ambiguous displayed price
- unrelated changes to scoring or evaluation modules

## Allowed files

Expected changes include:

- `src/pickflow_mcp_server/api.py`
- new fingerprint and supplier adapter/service modules
- `src/pickflow_mcp_server/server.py`
- synthetic test files
- README and TODO after implementation

Do not change contract definitions, Codex integration files, private local data, or unrelated evaluation behavior.

## Verification gates

Before handoff:

1. Run the complete offline test suite.
2. Run `git diff --check`.
3. Verify no credentials, raw response bodies, private paths, or ASIN fixtures were added.
4. Confirm existing MCP tools still register and behave as before.
5. Report exact live API call counts. Do not run a broad live test; a minimal smoke probe requires explicit quota approval.
6. Keep contract version `1.0` and explain any unmet contract need instead of silently changing it.

## Handoff report

Return:

- branch and commit hash;
- files changed;
- tests and results;
- live endpoints called and call counts, including zero;
- product-detail field coverage found in aggregate;
- known unknowns and deferred browser checks;
- confirmation that no raw/private data was committed.
