# TODO — PickFlow MCP Development Plan

## Now

- [x] 19-tool MCP server (cache + pipeline + analysis + session + 1688 sourcing)
- [x] Versioned nine-dimension scoring (`v1_legacy` reproduces 97-ASIN S+A=79.4%; `v2_semantic` is the corrected default)
- [x] Three-state hard filters (pass/fail/unknown) without treating missing fields as zero
- [x] SQLite ABA cache + keyword_detail result cache (7d TTL)
- [x] Concurrent API calls (Semaphore, 5x speedup)
- [x] Smart skip low-volume keywords
- [x] Unified traffic pagination, de-duplication, retries, and partial-failure metadata
- [x] Private local evaluation CLI with anonymous failure-stage diagnostics
- [x] 21 unit/regression tests across 4 test files

## Next

- [ ] **ABA segmented cache pull + recall-depth evaluation**

  Relevant niche traffic terms are concentrated around ABA pages 750-2500,
  while the current `cache_aba_pull` implementation fetches pages serially.
  Defer the large live run until API quota is available.

  - Add configurable concurrency (default 5) with bounded retries
  - Persist per-page status: success, empty, failed, item count, attempts, timestamp
  - Skip completed pages and support retrying failed pages only
  - Report progress/checkpoints for segments 750-999, 1000-1499, 1500-1999, 2000-2500
  - Measure three separate metrics: ABA traffic-keyword coverage, product-search
    recall at 2/5/10/20 pages, and end-to-end pipeline recall
  - Track marginal recall, API calls and elapsed time for each additional page range

- [x] **Enriched market_screen** — keyword_detail now returns brand_cr3, seller_cr3, ad_rev100/300, coupon_pct, price_range, top_brands/sellers (zero extra API)
- [ ] **ASIN detail cache** — cached product_detail results with configurable TTL
- [ ] **BSR regression model** — category-specific power-law estimates `sales = A * BSR^(-B)`

  sorftime's `monthly_sales_volume` frequently returns `5` or `0` (default when data not captured),
  but BSR is Amazon public data and always available. A BSR regression fills the gap and provides
  a cross-reference to validate sorftime's estimates.

  - Calibrated coefficients from amazon-omniscient project (52000, 0.80 for Home & Kitchen, etc.)
  - `product_detail` already returns `top_category` with numeric BSR — zero extra API calls
  - Integration: add `bsr_estimated_sales` field to `asin_score` output alongside sorftime's figure
  - Also useful for: identifying when sorftime's estimate is suspicious (e.g., 5 when BSR=15000)

- [ ] **product_trend integration** — market trend direction and seasonality

  Current scoring is a point-in-time snapshot. A product doing 500/mo with a -30% trend is very
  different from one doing 500/mo with a +30% trend. `keyword_trend` API (already available, unused)
  returns 12-month search volume history.

  - Extract from curve: trend direction (±% last 3 months), peak season month, stability (CV)
  - Integration: add `trend_direction` dimension to nine-dim scoring (rising +2pts, falling -2pts)
  - API cost: ~10 calls for S-tier markets only
  - Also feeds into seasonality risk assessment (e.g., Christmas-only vs year-round)

- [ ] **Multi-marketplace support** — per-site parameter profiles

  All current parameters (price sweet spot $15-45, BSR thresholds 3K-500K, review limit 150)
  were calibrated on US data from 97 ASINs. UK/JP/DE have different price norms, BSR scales,
  and FBA prevalence. Each marketplace needs its own parameter profile.

  - Config: `config/sweetspot.json` → `config/sweetspot_us.json`, `sweetspot_uk.json`, etc.
  - Tools accept `amz_site` parameter, defaulting to US
  - Hard filters also marketplace-specific (AU BSR max=20000 vs US=50000)
  - First targets: US (current), UK, DE — covers bulk/gift product markets
- [ ] **Pipeline resume** — checkpoint/resume for long-running cache_aba_pull

## Future

- [x] **1688 API exploration (sorftime)** — tested `ali1688_similar_product` + `product_search_from_image`
  
  Works for discovery (img search 100 + kw search 100 → 198 unique). Image search → MUST_MATCH filter
  narrows to ~30 candidates. But sorftime 1688 API only returns 16 fields (title, price, sales, store…)
  — no supplier-side product description or material/attribute fields. Cannot distinguish product
  form factor (ball vs cross vs sign) from title alone without visual/browser evidence.
  
  Strategy confirmed: img search 5p + kw search 1p → merge → MUST_MATCH core attrs → EXCLUDE noise.
  The first backend slice is now implemented; exact identity still requires Codex visual review
  and browser verification.

- [x] **1688 backend vertical slice** — deterministic fingerprint extractor, 1688 API adapters,
  candidate deduplication, hard mismatch gates, VisualReviewBundle for Codex vision. 3 MCP tools
  (`asin_fingerprint`, `supplier_search`, `supplier_compare_prepare`). 95 synthetic tests; full
  suite 120/120. Live smoke verified product detail, image search, keyword search, and all 3 tools
  without committing private identifiers or raw responses.

- [x] **Codex 1688 plugin/skill integration** — versioned under `codex-plugin/pickflow-1688/`

  Official plugin and skill validators pass. The repository-relative MCP configuration performs a
  real stdio handshake and exposes all 19 tools. One private live smoke used 3 upstream calls to
  normalize 120 candidates and cap the visual queue at 5; sidebar-browser review verified visible
  SKU, pack, MOQ, tier-price, and per-SKU package measurement fields without committing identifiers.
  Security sliders are a recoverable `verification_required` handoff, not a data error. Amazon image
  render failure remains unknown rather than becoming false positive evidence. Full suite 124/124.

- [ ] **1688 Official API integration** — supplier cost lookup and fba_profit automation

  Current `fba_profit` tool requires manual purchase cost input. Integrating supplier pricing
  would close the loop: ASIN discovery → profit calculation with real costs → sourcing decision.

  The omniscient project demonstrates a 1688 scraper approach (Playwright + rotating proxies)
  but it's fragile due to anti-bot countermeasures, login walls, and frequent layout changes.
  PickFlow will instead integrate with Alibaba's official 1688 API when available.

  - Source: 1688 Open Platform API (pending availability)
  - Input: product keyword or image → 1688 product search
  - Output: supplier name, FOB price range, MOQ, location, rating, lead time
  - Integration: New MCP tool `supplier_lookup` → feeds directly into `fba_profit`'s `purchase_cost_cny`
  - Bonus: compare multiple suppliers, flag best MOQ/price trade-off
  - Dependencies: API key, rate limits, authentication method TBD

- [ ] **Review sentiment analysis** — LLM-powered pain point extraction from competitor reviews
- [ ] **Product blueprint generator** — complaint-driven differentiation spec from review gaps
