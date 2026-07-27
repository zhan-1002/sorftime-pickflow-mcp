# Sorftime PickFlow MCP

Model Context Protocol server + Skill for Amazon product discovery. Powered by Sorftime API.

> Previously: `sorftime-pickflow-skill`. Consolidated into this repository.
> `skill/` contains the legacy Claude Code Skill (SKILL.md, methodology, config).
> `src/` contains the MCP server (16 tools, cache, pipeline, scoring).

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
