# Sorftime PickFlow MCP

Model Context Protocol server + Skill for Amazon product discovery. Powered by Sorftime API.

> Previously: `sorftime-pickflow-skill`. Consolidated into this repository.
> `skill/` contains the legacy Claude Code Skill (SKILL.md, methodology, config).
> `src/` contains the MCP server (15 tools, cache, pipeline, scoring).

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
| `asin_score` | Nine-dimension scoring for a single ASIN |
| `asin_score_batch` | Batch score multiple ASINs |

### Analysis Layer
| Tool | Description |
|------|-------------|
| `keyword_analyze` | Deep keyword analysis |
| `asin_reverse_traffic` | Reverse-lookup traffic keywords, check cache coverage |
| `asin_compare` | Side-by-side ASIN comparison |
| `pipeline_validate` | Test recall against known-good ASINs |

### Session
| Tool | Description |
|------|-------------|
| `session_status` | Cache status + API configuration |

## Install

```bash
git clone git@github.com:zhan-1002/sorftime-pickflow-skill.git
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
