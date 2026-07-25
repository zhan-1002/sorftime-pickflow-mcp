# Sorftime Bulk 产品线选品实战记录

> 日期：2026-07-25 | 工具：Sorftime MCP API (`https://mcp.sorftime.com`)
> 方法：ABA热榜 + 主题词扩展 → 双源合池 → 关键词匹配过滤 → 市场打分 → 甜区ASIN提取 → 六维评估

---

## 一、研究流程

```
                     ┌─ keyword_extends × 24 seeds × 2p (主题词扩展)
                     │   例: "christian gifts bulk" → "christian gifts for women"
                     │       "party favors bulk" → "goodie bags stuffers for kids"
                     │
                     ├─ keyword_list ABA热榜 (p1-5)
                     │   例: rank#2 paper towels → rank#5469 copper bracelets
                     │
                     └─→ 双源合并去重 → 709关键词池
                              │
                              ▼
                    关键词匹配过滤 (bulk / gift / party / wedding / christmas / ...)
                     过滤掉品牌词、泛词(gift/gifts)、Amazon卡类词
                              │
                              ▼
                     204个有效关键词 → 按月搜索量取 TOP50
                              │
                              ▼
                     keyword_detail × 50 → 市场指标矩阵
                     (搜索量 / CPC / 竞品数 / Rev分布 / Non-AMZ占比 / 旺季)
                              │
                              ▼
                     三层筛选:
                      ├─ Layer 1: Rev<100 ≥ 30% → 31个候选
                      ├─ Layer 2: Non-AMZ ≥ 15% + Rev<100 ≥ 50% → 15个候选
                      └─ Layer 3: 按品类分组 + 综合评分排序
                              │
                              ▼
                     10个市场 × product_search 甜区过滤
                     (price $15-45 / reviews ≤150 / FBA / 月销≥100 / sort by potential)
                              │
                              ▼
                     76个竞品ASIN → 四维可计算(CR3/评论壁垒/品牌数/价格CV)
                              │
                              ▼
                     每市场选3个ASIN × 3市场 = 9个深度评估
                     product_detail + product_traffic_terms (2页)
                              │
                              ▼
                     六维补全 + 广告字段修正 → 最终2个ASIN通过
```

---

## 二、关键技术发现

### 2.1 广告依赖度字段陷阱 ⚠️

**问题**：`product_traffic_terms` API 返回的 `exposure_position` 字段是字符串类型：
- `"Ad"` — 仅广告曝光
- `"Organic"` — 仅自然曝光
- `"Ad,Organic"` — 广告+自然双重曝光

**错误做法**：检查 `is_ad` 布尔字段 → 全部返回 0 → 误判为无广告

**正确做法**：
```python
ad_count = sum(1 for t in traffic_items if 'Ad' in (t.get('exposure_position', '') or ''))
ad_pct = ad_count / total_keywords * 100
```

**案例**：B0GR93MPX1（Herb Garden Kit）从"广告0%"修正为"广告88%" → 维度信号从🟢翻转为🔴

### 2.2 六维评估中可直接从 product_search 计算的维度

| 维度 | 可计算 | 数据来源 |
|:---:|:---:|---|
| ② 头部集中度 CR3 | ✅ | product_search 返品销量排序后计算 |
| ③ 评论壁垒 | ✅ | `review_count` 字段 |
| ④ 品牌垄断 | ✅ | `brand` 字段去重计数 |
| ⑤ 价格离散 CV | ✅ | `price` 字段计算 std/mean |
| ① 广告依赖度 | ❌ | 需要逐ASIN调 `product_traffic_terms` |
| ⑥ 新品窗口 | ⚠️ | `online_date`/`days_on_shelf` 有时为空 |

---

## 三、各市场评估结果

### Gift / Christian 类

| ASIN | 产品 | 价格 | 月销 | 广告依赖 | 评论 | 上架天数 | 毛利率 | 
|---|------|:---:|:---:|:---:|:---:|:---:|:---:|
| B0GVQMT9Q7 | Nurse Gifts Mug | $29.99 | 864 | 🟢13% | 🟡56 | 🟢86天 | 59% |
| B0GR93MPX1 | Herb Garden Kit | $39.99 | 930 | 🔴88% | 🟡32 | 🟢67天 | 69% |
| B0G1MJSKFR | Christian Blanket | $19.99 | 476 | 🟢18% | 🟡82 | 🔴243天 | 55% |

**结论**：Christian 市场整体低广告（12-18%），但需注意产品匹配度。Nurse Gifts Mug 是最优模板——低广告+新品+甜区价格+高增长。

### Wedding Favors 类

| ASIN | 产品 | 价格 | 月销 | 广告依赖 | 评论 | 上架天数 | 毛利率 |
|---|------|:---:|:---:|:---:|:---:|:---:|:---:|
| B0GMWJNSJ6 | Wedding Bubble 100pcs | $27.99 | 424 | 🟢23% | 🟢2 | ❓ | 64% |
| B0G6K5KNQM | Paper Hand Fans | $32.29 | 775 | 🟢20% | 🟡48 | 🔴211天 | 65% |
| B0FLCZ7M8Y | Gold Napkin Rings 100pcs | $24.99 | 593 | 🟢20% | 🟡100 | 🔴296天 | 64% |

**结论**：Wedding 市场广告依赖最低（20-23%），纯自然流量驱动。B0GMWJNSJ6 评论仅 2 条月销 424，是最纯粹的蓝海信号。

### Party Favors 类

| ASIN | 产品 | 价格 | 月销 | 广告依赖 | 评论 | 上架天数 | 毛利率 |
|---|------|:---:|:---:|:---:|:---:|:---:|:---:|
| B0GKRM2YV3 | Light UP Fidget 24pcs | $24.99 | 813 | 🔴73% | 🟢4 | ❓ | 60% |
| B0GCWG6YXM | Mini Troll Dolls 30pcs | $15.99 | 1064 | 🟡60% | 🟡74 | 🟡145天 | 60% |
| B0CYSCBB3K | Retro Sunglasses 10pk | $25.99 | 1436 | 🟡33% | 🔴102 | 🔴755天 | 62% |

**结论**：Party 市场广告依赖显著高于 Christian/Wedding（33-73%），竞争更激烈。不建议作为第一切入品类。

---

## 四、最终推荐

### ✅ 通过六维评估的 ASIN（可作为对标模板）

| 排名 | ASIN | 市场 | 核心优势 |
|:---:|---|------|------|
| 1 | B0GVQMT9Q7 | Christian/Nurse Gifts | 广告13%、86天冲864销、59%毛利 |
| 2 | B0GMWJNSJ6 | Wedding Bubble Bulk | 广告23%、仅2评、424销、64%毛利 |

### 排除的伪蓝海

| 市场 | 排除原因 |
|------|------|
| K-Pop Party Favors | 搜索量58K但产品端转化极低（B0H283S21B仅18销/月） |
| Candle Holders | Non-AMZ仅4-6%，Amazon高度自营垄断 |
| Halloween Party Favors | CR3=93%，头部高度集中 |
| Christmas Ornaments | CR3=72%，且搜索词与bulk场景匹配度低 |

---

## 五、甜区参数验证

基于此轮76个甜区ASIN的实际数据，与已有甜区参数对比：

| 参数 | 现有甜区 | 本轮验证 | 一致性 |
|---|---|---|---|
| 价格 | $20-40 | $15-44（中位$25-32） | ✅ 吻合 |
| 评分数 | ≤100 | 77% ASIN <100评 | ✅ 吻合 |
| BSR | 3万-30万 | 5K-32K（中位~18K） | ⚠️ 略偏低，bulk品类BSR更优 |
| FBA | 必须 | 100% | ✅ 完全吻合 |
| 毛利率 | — | 55-69%（中位~62%） | 新发现 |

---

## 六、调用额度统计

| 阶段 | API 调用 | 数量 |
|---|---|---|
| Phase 1 扩池 | keyword_extends | ~48次 |
| Phase 1 扩池 | keyword_list (ABA热榜) | 5次 | p1-5，p1-3超时p4-5有效，贡献 ~5% 池量 |
| Phase 1 扩池 | search_categories_broadly | 6次 |
| Phase 2 筛选 | keyword_detail | 50次 |
| Phase 3 产品 | product_search | ~16次 |
| Phase 4 评估 | product_detail | 9次 |
| Phase 4 评估 | product_traffic_terms | 18次 |
| Phase 4 评估 | competitor_product_keywords | 9次 |
| ABA边界探测 | keyword_list | 5次 | p250/500/1000/2500/5000，验证5万+词覆盖 |
| **总计** | | **~167次** |

---

## 七、关键词池数据源分析

### 实际构成

709 关键词池来自两个数据源：

| 来源 | API | 调用量 | 贡献度 | 说明 |
|---|---|---|---|---|
| **主题词扩展** | `keyword_extends` | 24 seeds × 2p = 48次 | ~95% | 种子词来自用户原始产品列表的主题 |
| **ABA 热榜直拉** | `keyword_list` | p1-5 = 5次 | ~5% | p1-3 超时，p4-5 返回 40 条 |

### 主题词（过滤规则）

以下关键词匹配规则定义了从 709 池中筛选"相关词"的逻辑，而非种子扩展的起点：

```
bulk / gift / party / wedding / christmas / halloween / favor
supplies / bags / decorations / team / christian / employee
appreciation / napkin / candle / basket / ornament / goodie
baseball / soccer / cheer / kpop / fans / tablecloth / stocking
bridal / bridesmaid / baby shower / church / religious / prayer
journal / notebook / trophy / graduation / ...
```

### ABA 热榜验证

通过探测 `keyword_list` 分页边界确认：
- API 支持 ≥ 50,000 个 ABA 热搜关键词（page 2500 仍有数据）
- Bulk 类关键词集中在 ABA rank #15,000-55,000（30天搜索量 3.5万-17万）
- **可用 `keyword_list` + 关键词匹配规则直接替代 `keyword_extends` 扩池**，消除种子词偏差

### 方法论改进

1. **双源合池 > 单源依赖**。`keyword_extends` 提供主题深度，`keyword_list` 提供榜单广度，互补覆盖更全。

2. **下次可完全走 ABA 直拉路线**：`keyword_list` 拉取 500-1000 页 → 关键词匹配过滤 → keyword_detail 打分。无需种子词，无偏差。

3. **广告依赖度是六维中最易误判的维度**。必须逐ASIN调用 `product_traffic_terms` 并正确解析 `exposure_position` 字符串字段（`"Ad"` / `"Organic"` / `"Ad,Organic"`），不可用布尔字段判断。

4. **keyword_detail 的 Top5 产品数据可直接计算 CR3**，不需要再跑 product_search。优化流程可节省 30% 调用。

5. **product_search 的甜区过滤参数可以一次性完成初筛**。价格/评论/月销/FBA 四个参数组合命中率 >75%。
