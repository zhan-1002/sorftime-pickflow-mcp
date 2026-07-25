"""
FBA Profit Calculator — unit economics for Amazon FBA products.
Based on validated spreadsheet model + 2026 hidden fees.
"""
from dataclasses import dataclass, field


@dataclass
class FBAInput:
    """Input parameters for FBA profit calculation."""
    # Required fields (no defaults)
    selling_price_usd: float           # Target selling price ($)
    purchase_cost_cny: float           # 1688/supplier unit price (¥)

    # Core costs (with defaults)
    fba_shipping_cny: float = 16.08    # China→FBA warehouse freight (¥)
    commission_rate_pct: float = 15.0  # Amazon referral fee (%)
    fba_delivery_usd: float = 7.30     # Amazon fulfillment fee ($)

    # Pricing
    discount_pct: float = 0.0          # Coupon/discount rate (%)

    # Advertising
    ad_budget_usd: float = 10.0        # Daily PPC budget ($)
    cpc_usd: float = 1.0               # Cost per click ($)
    conversion_rate_pct: float = 18.0  # Ad conversion rate (%)

    # Settings
    exchange_rate: float = 6.8         # USD to CNY
    organic_traffic: float = 0.0       # Daily organic clicks (0 = pure ad for conservative estimate)

    # 2026 Hidden fees (optional, defaults approximate)
    fuel_surcharge_pct: float = 3.5    # % of FBA delivery fee
    monthly_storage_usd: float = 0.15  # $ per unit per month
    inbound_placement_usd: float = 0.30  # $ per unit
    return_rate_pct: float = 5.0       # Return rate (%)
    prep_fee_usd: float = 0.30         # Prep/labeling fee ($)


@dataclass
class FBAResult:
    """Output of FBA profit calculation."""
    # Breakeven
    breakeven_price_usd: float

    # Unit economics
    unit_profit_usd: float
    unit_profit_cny: float
    net_profit_margin_pct: float
    roi_pct: float
    sales_proceed_usd: float
    actual_cost_usd: float

    # Daily/Monthly
    ad_clicks: float
    total_clicks: float
    daily_orders: float
    monthly_orders: float
    daily_profit_usd: float
    daily_profit_cny: float
    monthly_profit_usd: float
    monthly_profit_cny: float

    # Efficiency
    acos_pct: float
    tacos_pct: float  # Total ACOS including hidden fees

    # Hidden fees breakdown
    hidden_fees_usd: float
    hidden_fees_breakdown: dict

    # Assessment
    verdict: str  # "STRONG", "VIABLE", "MARGINAL", "NOT_RECOMMENDED"
    verdict_reasons: list[str]


def calculate(input: FBAInput) -> FBAResult:
    """Run FBA profit calculation."""

    # === Layer 1: Traffic & Orders ===
    ad_clicks = input.ad_budget_usd / max(input.cpc_usd, 0.01)
    total_clicks = input.organic_traffic + ad_clicks
    daily_orders = total_clicks * (input.conversion_rate_pct / 100)
    monthly_orders = daily_orders * 30

    # === Hidden fees (2026) ===
    fuel = input.fba_delivery_usd * (input.fuel_surcharge_pct / 100)
    return_loss = input.selling_price_usd * (input.return_rate_pct / 100)
    hidden_total = round(fuel + input.monthly_storage_usd + input.inbound_placement_usd + return_loss + input.prep_fee_usd, 2)

    hidden_breakdown = {
        "fuel_surcharge": round(fuel, 2),
        "monthly_storage": input.monthly_storage_usd,
        "inbound_placement": input.inbound_placement_usd,
        "return_loss": round(return_loss, 2),
        "prep_fee": input.prep_fee_usd,
    }

    # === Layer 2: Breakeven Price ===
    cost_cny = input.purchase_cost_cny + input.fba_shipping_cny
    cost_usd = cost_cny / input.exchange_rate
    # Breakeven: covers purchase + shipping(usd) + FBA delivery + commission + hidden fees
    breakeven = (cost_usd + input.fba_delivery_usd + hidden_total) / (1 - input.commission_rate_pct / 100)

    # === Layer 3: Unit Profit ===
    discounted_price = input.selling_price_usd - (input.selling_price_usd * input.discount_pct / 100)
    commission_amt = discounted_price * (input.commission_rate_pct / 100)
    unit_profit = discounted_price - commission_amt - input.fba_delivery_usd - cost_usd - hidden_total
    unit_profit_cny = unit_profit * input.exchange_rate

    sales_proceed = discounted_price - commission_amt - input.fba_delivery_usd
    actual_cost = discounted_price - unit_profit
    net_margin = (unit_profit / max(discounted_price, 0.01)) * 100
    roi = (unit_profit_cny / max(cost_cny, 0.01)) * 100

    # === Layer 4: Period Profit ===
    daily_profit = unit_profit * daily_orders - input.ad_budget_usd
    daily_profit_cny = daily_profit * input.exchange_rate
    monthly_profit = daily_profit * 30
    monthly_profit_cny = daily_profit_cny * 30

    # === Layer 5: Efficiency ===
    ad_sales = daily_orders * discounted_price
    acos = (input.ad_budget_usd / max(ad_sales, 0.01)) * 100
    # TACOS: total ad + hidden fees as % of sales
    tacos = ((input.ad_budget_usd + hidden_total * daily_orders) / max(ad_sales, 0.01)) * 100

    # === Verdict ===
    reasons = []
    if net_margin >= 30:
        verdict = "STRONG"
        reasons.append(f"Net margin {net_margin:.0f}% >= 30%")
    elif net_margin >= 20:
        verdict = "VIABLE"
        reasons.append(f"Net margin {net_margin:.0f}% in 20-30% range")
    elif net_margin >= 10:
        verdict = "MARGINAL"
        reasons.append(f"Net margin {net_margin:.0f}% borderline")
    else:
        verdict = "NOT_RECOMMENDED"
        reasons.append(f"Net margin {net_margin:.0f}% too low")

    if roi >= 150:
        reasons.append(f"ROI {roi:.0f}% >= 150%")
    elif roi < 80:
        reasons.append(f"ROI {roi:.0f}% < 80% — check sourcing cost")
        if verdict == "STRONG":
            verdict = "VIABLE"

    if acos > 25:
        reasons.append(f"ACOS {acos:.0f}% > 25% — ad-heavy")
        if verdict == "STRONG":
            verdict = "VIABLE"
    elif acos < 15:
        reasons.append(f"ACOS {acos:.0f}% efficient")

    if input.selling_price_usd < breakeven:
        reasons.append(f"Price ${input.selling_price_usd:.2f} < breakeven ${breakeven:.2f} — losing money")
        verdict = "NOT_RECOMMENDED"

    if hidden_total > 2.0:
        reasons.append(f"Hidden fees ${hidden_total:.2f}/unit — consider optimization")

    return FBAResult(
        breakeven_price_usd=round(breakeven, 2),
        unit_profit_usd=round(unit_profit, 2),
        unit_profit_cny=round(unit_profit_cny, 2),
        net_profit_margin_pct=round(net_margin, 1),
        roi_pct=round(roi, 1),
        sales_proceed_usd=round(sales_proceed, 2),
        actual_cost_usd=round(actual_cost, 2),
        ad_clicks=round(ad_clicks, 1),
        total_clicks=round(total_clicks, 1),
        daily_orders=round(daily_orders, 1),
        monthly_orders=round(monthly_orders, 1),
        daily_profit_usd=round(daily_profit, 2),
        daily_profit_cny=round(daily_profit_cny, 2),
        monthly_profit_usd=round(monthly_profit, 2),
        monthly_profit_cny=round(monthly_profit_cny, 2),
        acos_pct=round(acos, 1),
        tacos_pct=round(tacos, 1),
        hidden_fees_usd=hidden_total,
        hidden_fees_breakdown=hidden_breakdown,
        verdict=verdict,
        verdict_reasons=reasons,
    )
