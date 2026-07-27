# Sorftime PickFlow MCP

Model Context Protocol server + Skill for Amazon product discovery. Powered by Sorftime API.

> Previously: `sorftime-pickflow-skill`. Consolidated into this repository.
> `skill/` contains the legacy Claude Code Skill (SKILL.md, methodology, config).
> `src/` contains the MCP server (19 tools, cache, pipeline, scoring, 1688 sourcing).

## Tools

### Cache Layer (offline)
| Tool | Description |
|------|-------------|
| `cache_aba_pull` | Pull ABA keyword pages into local SQLite |
| `cache_status` | Show cache coverage and category breakdown |
| `cache_query` | Search cached keywords by category |
| `cache_term_distribution` | Show where a term peaks in ABA rankings |
| `cache_clear` | Clear cached pages |

### Pipeline Layer (API + cache)
| Tool | Description |
|------|-------------|
| `pool_build` | Build keyword pool from cache (no API) |
| `market_screen` | Run keyword_detail + enriched fields (brand CR3, seller CR3, ad review dist, coupon, price range) |
| `asin_discover` | Find ASINs with sweetspot filters |
| `asin_score` | Versioned nine-dimension scoring with completeness/confidence |
| `asin_score_batch` | Batch score ASINs with unified traffic pagination |

### Analysis Layer
| Tool | Description |
|------|-------------|
| `keyword_analyze` | Deep keyword analysis |
| `asin_reverse_traffic` | Reverse-lookup traffic keywords, check cache coverage |
| `asin_compare` | Side-by-side ASIN comparison |
| `pipeline_validate` | Test recall against known-good ASINs |
| `fba_profit` | Calculate FBA unit economics and hidden fees |

### 1688 Sourcing Layer
| Tool | Description |
|------|-------------|
| `asin_fingerprint` | Build normalized product fingerprint from Sorftime product_detail |
| `supplier_search` | Search 1688.com via image + keyword, deduplicate candidates |
| `supplier_compare_prepare` | Deterministic comparison gates + VisualReviewBundle for Codex vision |

### Session
| Tool | Description |
|------|-------------|
| `session_status` | Cache status + API configuration |

## Install

```bash
git clone git@github.com:zhan-1002/sorftime-pickflow-mcp.git
cd sorftime-pickflow-mcp
uv sync
```

## Configure Claude Code

In `~/.claude.json`, under the project entry, add:

```json
"mcpServers": {
  "pickflow": {
    "command": "C:/Users/asus/AppData/Roaming/Python/Python313/Scripts/uv.exe",
    "args": [
      "--directory",
      "C:/Users/asus/sorftime-pickflow-mcp",
      "run",
      "pickflow-mcp-server"
    ]
  }
}
```

Requires Sorftime API configured in `~/.mcp.json`.

## Scoring versions

- `v2_semantic` is the default. It treats `monthly_sales_volume` as a monthly
  estimate (`sales / 30`), preserves missing inputs as missing dimensions, and
  returns `data_completeness` plus `score_confidence`.
- `v1_legacy` reproduces the historical formula used for the original 97-ASIN
  calibration. Use it only for comparison with earlier reports.

Traffic terms are fetched through one pagination and de-duplication path for
single scoring, batch scoring, reverse traffic and ASIN comparison. Partial or
failed traffic responses are reported explicitly rather than treated as a true
zero-keyword result.

## Private evaluation

The repository does not contain evaluation ASINs, keywords or credentials. A
local CSV named `test_set_parsed.csv` with `asin,keyword` columns can be evaluated
without printing identifiers:

```bash
pickflow-evaluate --data-dir "D:/private/pickflow-eval" --limit 5
```

Use `--limit 0` for the complete private set. Add `--output` only when an
anonymous per-case diagnostic JSON is needed; standard output remains aggregate.

## 1688 Supplier Matching

The `asin_fingerprint` → `supplier_search` → `supplier_compare_prepare` pipeline
builds a product fingerprint, searches 1688.com by image and keyword, deduplicates
candidates, and records deterministic evidence for product form, material,
item/package dimensions, and quantity. At most five non-rejected candidates are packaged into a
`VisualReviewBundle` for Codex-native vision review.

Price comparability follows strict rules: a unit price is only published when
SKU, pack quantity, and tier are all known. Ambiguous prices are flagged and
FBA profit calculation is withheld. See `docs/1688-codex-architecture.md` for
the full responsibility boundary and evidence policy.

The version-controlled Codex plugin is in `codex-plugin/pickflow-1688/`. It
starts this repository's `.venv` MCP server through a relative path and reads
`SORFTIME_MCP_URL` from the plugin host environment; no credential is stored in
the manifest. Its sourcing skill orchestrates the three backend tools, caps
visual review at five candidates, and uses the sidebar browser to verify visible
SKU, pack, MOQ, price-tier, and package-measurement facts. A 1688 security
slider is treated as `verification_required`: the current tab is handed to the
user for manual verification instead of being retried or misreported as missing
product data.
