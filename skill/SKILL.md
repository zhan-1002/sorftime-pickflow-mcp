---
name: sorftime-pickflow
description: >
  Sorftime-powered Amazon product discovery. Pulls ABA hot keywords, screens markets,
  discovers ASINs via sweetspot filtering, and scores them on nine dimensions.
  For bulk, gift, party, wedding, seasonal, and home-decor product lines.
---

# Sorftime PickFlow

## What It Does

Discovers Amazon product opportunities in four stages:

1. **Keyword Pool** — pulls ABA trending keywords from Sorftime, filters by product category
2. **Market Screening** — scores keywords on demand, competition, review barriers, and Amazon monopoly
3. **ASIN Discovery** — runs sweetspot-filtered product searches against candidate markets
4. **Nine-Dimension Scoring** — evaluates each ASIN on sales velocity, profit, barriers, traffic, growth, pricing, breadth, positioning, and listing quality

Outputs ranked, scored ASINs ready for sourcing evaluation.

## When to Use

- Researching new product lines (bulk gifts, party supplies, wedding favors, seasonal decor)
- Expanding keyword coverage for an existing niche
- Validating market entry feasibility before committing to a product

## How to Run

The skill invokes `scripts/pipeline.py` step by step. It reads configuration from `config/*.json` and writes output CSVs to `data/`.

Adjust `config/filter_words.json` to target different categories before running.

## Prerequisites

- Sorftime MCP configured at `~/.mcp.json` under the key `sorftime-mcp`
- Python 3.9+, `curl`, standard library only (no pip dependencies)

## Important Notes

- **`exposure_position` field**: Sorftime returns string values (`"Ad"`, `"Organic"`, `"Ad,Organic"`), not booleans. Parse with `'Ad' in value`.
- **Days on shelf = 0**: common for recent listings. Scored as neutral (5/10) rather than penalized.
- **keyword_detail** returns empty for niche long-tail keywords below ABA reporting threshold. These are silently skipped.
- All data outputs are gitignored. API keys live in `~/.mcp.json` and are never committed.
