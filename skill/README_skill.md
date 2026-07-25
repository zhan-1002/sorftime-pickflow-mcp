# Sorftime PickFlow

> Amazon 产品机会发现引擎 — 关键词→市场筛选→ASIN发现→多维评估，全管道自动化。

基于 Sorftime MCP API，从 ABA 热搜关键词出发，经过四层漏斗筛选，输出带评分的候选产品清单。

## 为什么

传统的 Amazon 选品依赖人工逐个反查 ASIN、手工整理竞品数据。PickFlow 把这个过程自动化：

- **关键词池** 从 ABA 热榜无偏构建（覆盖 50,000+ 关键词），消除种子词偏差
- **市场筛选** 基于搜索量/CPC/评论分布/品牌垄断度多维度过滤
- **ASIN 发现** 用甜区参数（价格/评论/FBA/月销）直接输出候选
- **九维评分** 替换传统的 G/Y/R 三元判断，连续评分 + 可排序

## 安装

```bash
git clone git@github.com:zhan-1002/sorftime-pickflow-skill.git ~/.claude/skills/sorftime-pickflow
```

## 前置

在 `~/.mcp.json` 中配置 Sorftime API：

```json
{
  "sorftime-mcp": {
    "type": "http",
    "url": "https://mcp.sorftime.com?key=YOUR_API_KEY",
    "disabled": false
  }
}
```

## 使用

```bash
# 全管道运行
python scripts/pipeline.py --pages 500 --limit 80

# 分步执行
python scripts/pipeline.py --step 1 --pages 500   # ABA 关键词拉取
python scripts/pipeline.py --step 2 --limit 80     # 市场打分
python scripts/pipeline.py --step 3                 # ASIN 发现
python scripts/pipeline.py --step 4                 # 九维评分
```

## 配置

| 文件 | 内容 | 可调参数 |
|------|------|------|
| `config/filter_words.json` | 关键词匹配规则 | 品类词库、黑名单、最小词长 |
| `config/sweetspot.json` | 甜区过滤 | 价格带、评论上限、月销底线 |
| `config/weights.json` | 评分权重 | 九维权重、分档阈值 |

## 目录

```
sorftime-pickflow/
├── SKILL.md              # Claude Code skill 入口
├── config/               # 参数配置（JSON）
├── scripts/
│   ├── pipeline.py       # 主流水线
│   ├── scoring.py        # 九维评分引擎
│   └── common.py         # API 调用与工具函数
├── methodology/          # 方法论文档
└── data/                 # 产出 CSV（gitignored）
```

## 关键发现

- **`exposure_position` 字段陷阱**: Sorftime API 返回字符串 `"Ad"` / `"Organic"` / `"Ad,Organic"`，非布尔值。用 `'Ad' in field` 判断广告依赖度，否则全部误判为 0%
- **上架天数不是淘汰信号**: 改为与日销交叉评分（增长势能维度），而非硬门淘汰
- **ABA 池覆盖**: bulk/gift 类精准关键词集中在 ABA rank #15,000-55,000，对应 page 750-2,500

## 许可

MIT
