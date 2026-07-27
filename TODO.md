# TODO — PickFlow MCP Development Plan

## Now

- [x] 15-tool MCP server (cache + pipeline + analysis + session)
- [x] Nine-dimension weighted scoring (validated on 97 ASINs, S+A=79.4%)
- [x] Hard disqualification filters (price<$10, zero sales, not FBA)
- [x] SQLite ABA cache + keyword_detail result cache (7d TTL)
- [x] Concurrent API calls (Semaphore, 5x speedup)
- [x] Smart skip low-volume keywords
- [x] 12 unit tests across 3 test files

## Next

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
  — no product description or material/attribute fields. Cannot distinguish product form factor
  (ball vs cross vs sign) from title alone. On hold until API adds description field.
  
  Strategy confirmed: img search 5p + kw search 1p → merge → MUST_MATCH core attrs → EXCLUDE noise.
  Pending: `supplier_lookup` MCP tool. Blocked by API field limitation.

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
