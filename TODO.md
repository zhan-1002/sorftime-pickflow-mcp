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

- [ ] **ASIN detail cache** — cached product_detail results with configurable TTL
- [ ] **BSR regression model** — category-specific power-law estimates `sales = A * BSR^(-B)` as fallback when sorftime returns no sales data
- [ ] **Trend dimension** — integrate `product_trend` API data into growth_potential scoring
- [ ] **Multi-marketplace support** — parameterize sweetspot/hard_filter thresholds by marketplace (US/UK/DE/JP)
- [ ] **Pipeline resume** — checkpoint/resume for long-running cache_aba_pull

## Future

- [ ] **1688 Official API integration** — supplier cost lookup

  The omniscient project demonstrates a 1688 scraper approach (Playwright + rotating proxies)
  but it's fragile due to anti-bot countermeasures, login walls, and frequent layout changes.
  PickFlow will instead integrate with Alibaba's official 1688 API when available,
  providing reliable supplier data (FOB price, MOQ, lead time) without scraping.

  - Source: 1688 Open Platform API (pending availability)
  - Input: product keywords or image → 1688 search
  - Output: supplier name, FOB price range, MOQ, location, rating
  - Integration point: New MCP tool `supplier_lookup` in analysis layer
  - Dependencies: API key, rate limits TBD

- [ ] **Review sentiment analysis** — LLM-powered pain point extraction from competitor reviews
- [ ] **Product blueprint generator** — complaint-driven differentiation spec from review gaps
