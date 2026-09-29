"""Terminal rendering: formatted cost impact report using rich."""
from __future__ import annotations

import json as json_mod
import sys
from decimal import Decimal, ROUND_HALF_UP

from costguard.cache import CacheStats
from costguard.delta import CostDelta

# Currency symbols
CURRENCY_SYMBOLS = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "INR": "₹",
}


def _sym(currency: str) -> str:
    """Get currency symbol."""
    return CURRENCY_SYMBOLS.get(currency, currency + " ")


def _fmt_money(amount: Decimal, currency: str = "USD") -> str:
    """Format a monetary amount with currency symbol."""
    sym = _sym(currency)
    return f"{sym}{amount:,.2f}"


def _fmt_delta(amount: Decimal, currency: str = "USD") -> str:
    """Format a delta with +/- sign."""
    sym = _sym(currency)
    if amount > 0:
        return f"+{sym}{amount:,.2f}"
    elif amount < 0:
        return f"-{sym}{abs(amount):,.2f}"
    else:
        return f"{sym}0.00"


def render_report(
    deltas: list[CostDelta],
    cache_stats: CacheStats,
    max_increase: float | None,
    passed: bool,
    verdict_message: str,
    currency: str = "USD",
    use_markdown: bool = False,
    use_json: bool = False,
    unpriced_count: int = 0,
    zero_deltas_hidden: int = 0,
):
    """Render the cost impact report to stdout.

    Supports three output modes:
    - Default: Rich terminal table (with color if TTY, plain if piped)
    - --markdown: GitHub PR comment formatted table
    - --json: Machine-readable JSON output
    """
    if use_json:
        _render_json(deltas, cache_stats, max_increase, passed, verdict_message, currency, unpriced_count, zero_deltas_hidden)
        return

    if use_markdown:
        _render_markdown(deltas, cache_stats, max_increase, passed, verdict_message, currency, unpriced_count, zero_deltas_hidden)
        return

    # Default: use rich if TTY, fallback to plain text
    is_tty = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    if is_tty:
        try:
            _render_rich(deltas, cache_stats, max_increase, passed, verdict_message, currency, unpriced_count, zero_deltas_hidden)
            return
        except ImportError:
            pass

    _render_plain(deltas, cache_stats, max_increase, passed, verdict_message, currency, unpriced_count, zero_deltas_hidden)


def _render_rich(
    deltas: list[CostDelta],
    cache_stats: CacheStats,
    max_increase: float | None,
    passed: bool,
    verdict_message: str,
    currency: str,
    unpriced_count: int,
    zero_deltas_hidden: int,
):
    """Render using the rich library for colored terminal output."""
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text
    from rich.panel import Panel

    console = Console()

    # Header banner
    console.print()
    console.print(
        Panel(
            "[bold white]COSTGUARD: Azure Infrastructure Cost Impact Report[/bold white]",
            style="bold cyan",
            width=100,
        )
    )

    # Resource table
    table = Table(show_header=True, header_style="bold magenta", width=100)
    table.add_column("Resource Address", style="white", min_width=35)
    table.add_column("Action", style="cyan", min_width=8)
    table.add_column("Region", style="dim", min_width=8)
    table.add_column("SKU / Meter", style="yellow", min_width=20)
    table.add_column(f"Old ({_sym(currency)}/mo)", justify="right", min_width=10)
    table.add_column(f"New ({_sym(currency)}/mo)", justify="right", min_width=10)
    table.add_column(f"Delta ({_sym(currency)}/mo)", justify="right", min_width=12)

    for d in deltas:
        is_unpriced = getattr(d, 'unpriced', False)
        if is_unpriced:
            old_str = "n/a"
            new_str = "n/a"
            delta_str = "n/a"
            delta_style = "dim"
            sku_label = f"[UNPRICED] {d.sku_label}"
        else:
            old_str = _fmt_money(d.old_monthly, currency)
            new_str = _fmt_money(d.new_monthly, currency)
            delta_str = _fmt_delta(d.delta, currency)
            delta_style = "green" if d.delta < 0 else ("red" if d.delta > 0 else "dim")
            sku_label = d.sku_label

        table.add_row(
            d.address,
            d.action,
            d.region,
            sku_label,
            old_str,
            new_str,
            Text(delta_str, style=delta_style),
        )

    console.print(table)

    # Financial summary
    total_old = sum(d.old_monthly.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) for d in deltas if not getattr(d, 'unpriced', False))
    total_new = sum(d.new_monthly.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) for d in deltas if not getattr(d, 'unpriced', False))
    net_delta = sum(d.delta.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) for d in deltas if not getattr(d, 'unpriced', False))

    console.print()
    console.print("[bold]FINANCIAL SUMMARY:[/bold]")
    console.print(f"  Prior Monthly Total: {_fmt_money(total_old, currency)}/mo")
    console.print(f"  Projected Monthly Total: {_fmt_money(total_new, currency)}/mo")

    delta_color = "green" if net_delta <= 0 else "red"
    cache_info = f"[Cache: {cache_stats.hits} hits, {cache_stats.api_lookups} API lookup{'s' if cache_stats.api_lookups != 1 else ''}]"
    console.print(
        f"  Net Monthly Impact: [{delta_color}]{_fmt_delta(net_delta, currency)}/mo[/{delta_color}]"
        f"   {cache_info}"
    )

    # Policy verdict
    console.print()
    console.print("[bold]POLICY VERDICT:[/bold]")
    if max_increase is not None:
        console.print(f"  Budget Threshold: +{_sym(currency)}{Decimal(str(max_increase)):,.2f}/mo")
    verdict_color = "bold green" if passed else "bold red"
    console.print(f"  Status: [{verdict_color}]{verdict_message}[/{verdict_color}]")

    if not passed:
        console.print()
        console.print(
            "[bold red][CIRCUIT BREAKER] CostGuard: Budget threshold breached. "
            "Deployment blocked.[/bold red]"
        )

    if zero_deltas_hidden > 0:
        console.print(f"[dim]{zero_deltas_hidden} metadata-only change(s) with $0.00 impact hidden[/dim]")
    if unpriced_count > 0:
        console.print(f"[bold yellow]{unpriced_count} resource(s) UNPRICED, total may be understated[/bold yellow]")

    console.print()


def _render_plain(
    deltas: list[CostDelta],
    cache_stats: CacheStats,
    max_increase: float | None,
    passed: bool,
    verdict_message: str,
    currency: str,
    unpriced_count: int,
    zero_deltas_hidden: int,
):
    """Render plain text output (for piped output or when rich is unavailable)."""
    sep = "=" * 100
    dash = "-" * 100

    print(sep)
    print("COSTGUARD: Azure Infrastructure Cost Impact Report")
    print(sep)
    # Header
    print(
        f"{'Resource Address':<38s} {'Action':<8s} {'Region':<10s} "
        f"{'SKU / Meter':<25s} {'Old ($/mo)':>10s} {'New ($/mo)':>10s} {'Delta ($/mo)':>12s}"
    )
    print(dash)

    for d in deltas:
        is_unpriced = getattr(d, 'unpriced', False)
        if is_unpriced:
            old_str = "n/a"
            new_str = "n/a"
            delta_str = "n/a"
            sku_label = f"[UNPRICED] {d.sku_label}"
        else:
            old_str = _fmt_money(d.old_monthly, currency)
            new_str = _fmt_money(d.new_monthly, currency)
            delta_str = _fmt_delta(d.delta, currency)
            sku_label = d.sku_label

        print(
            f"{d.address:<38s} {d.action:<8s} {d.region:<10s} "
            f"{sku_label:<25s} {old_str:>10s} "
            f"{new_str:>10s} {delta_str:>12s}"
        )

    print(dash)

    total_old = sum(d.old_monthly for d in deltas)
    total_new = sum(d.new_monthly for d in deltas)
    net_delta = sum(d.delta for d in deltas)

    print("FINANCIAL SUMMARY:")
    print(f"  Prior Monthly Total: {_fmt_money(total_old, currency)}/mo")
    print(f"  Projected Monthly Total: {_fmt_money(total_new, currency)}/mo")
    cache_info = f"[Cache: {cache_stats.hits} hits, {cache_stats.api_lookups} API lookup{'s' if cache_stats.api_lookups != 1 else ''}]"
    print(f"  Net Monthly Impact: {_fmt_delta(net_delta, currency)}/mo   {cache_info}")

    print(dash)
    print("POLICY VERDICT:")
    if max_increase is not None:
        print(f"  Budget Threshold: +{_sym(currency)}{Decimal(str(max_increase)):,.2f}/mo")
    print(f"  Status: {verdict_message}")

    if not passed:
        print()
        print("[CIRCUIT BREAKER] CostGuard: Budget threshold breached. Deployment blocked.")

    if zero_deltas_hidden > 0:
        print(f"{zero_deltas_hidden} metadata-only change(s) with $0.00 impact hidden")
    if unpriced_count > 0:
        print(f"{unpriced_count} resource(s) UNPRICED, total may be understated")

    print(sep)


def _render_markdown(
    deltas: list[CostDelta],
    cache_stats: CacheStats,
    max_increase: float | None,
    passed: bool,
    verdict_message: str,
    currency: str,
    unpriced_count: int,
    zero_deltas_hidden: int,
):
    """Render GitHub PR comment formatted markdown output."""
    sym = _sym(currency)
    total_old = sum(d.old_monthly.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) for d in deltas if not getattr(d, 'unpriced', False))
    total_new = sum(d.new_monthly.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) for d in deltas if not getattr(d, 'unpriced', False))
    net_delta = sum(d.delta.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) for d in deltas if not getattr(d, 'unpriced', False))

    emoji = "✅" if passed else "🚨"
    print(f"## {emoji} CostGuard: Azure Infrastructure Cost Impact Report")
    print()

    if deltas:
        print(f"| Resource Address | Action | Region | SKU / Meter | Old ({sym}/mo) | New ({sym}/mo) | Delta ({sym}/mo) |")
        print("|:---|:---:|:---:|:---|---:|---:|---:|")
        for d in deltas:
            print(
                f"| `{d.address}` | {d.action} | {d.region} | {d.sku_label} | "
                f"{_fmt_money(d.old_monthly, currency)} | {_fmt_money(d.new_monthly, currency)} | "
                f"**{_fmt_delta(d.delta, currency)}** |"
            )
        print()

    print("### Financial Summary")
    print()
    print(f"- **Prior Monthly Total:** {_fmt_money(total_old, currency)}/mo")
    print(f"- **Projected Monthly Total:** {_fmt_money(total_new, currency)}/mo")
    cache_info = f"(Cache: {cache_stats.hits} hits, {cache_stats.api_lookups} API lookups)"
    print(f"- **Net Monthly Impact:** {_fmt_delta(net_delta, currency)}/mo {cache_info}")
    print()

    print("### Policy Verdict")
    print()
    if max_increase is not None:
        print(f"- **Budget Threshold:** +{sym}{Decimal(str(max_increase)):,.2f}/mo")
    status_emoji = "✅" if passed else "❌"
    print(f"- **Status:** {status_emoji} {verdict_message}")

    if not passed:
        print()
        print("> 🚨 **[CIRCUIT BREAKER]** CostGuard: Budget threshold breached. Deployment blocked.")

    if zero_deltas_hidden > 0:
        print(f"> 💡 _{zero_deltas_hidden} metadata-only change(s) with $0.00 impact hidden_")
    if unpriced_count > 0:
        print(f"> ⚠️ **{unpriced_count} resource(s) UNPRICED, total may be understated**")

def _render_json(
    deltas: list[CostDelta],
    cache_stats: CacheStats,
    max_increase: float | None,
    passed: bool,
    verdict_message: str,
    currency: str,
    unpriced_count: int,
    zero_deltas_hidden: int,
):
    """Render machine-readable JSON output."""
    total_old = sum(d.old_monthly.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) for d in deltas if not getattr(d, 'unpriced', False))
    total_new = sum(d.new_monthly.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) for d in deltas if not getattr(d, 'unpriced', False))
    net_delta = sum(d.delta.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) for d in deltas if not getattr(d, 'unpriced', False))

    output = {
        "currency": currency,
        "resources": [
            {
                "address": d.address,
                "action": d.action,
                "region": d.region,
                "sku": d.sku_label,
                "old_monthly": float(d.old_monthly),
                "new_monthly": float(d.new_monthly),
                "delta": float(d.delta),
                "tags": d.tags,
            }
            for d in deltas
        ],
        "summary": {
            "prior_monthly_total": float(total_old),
            "projected_monthly_total": float(total_new),
            "net_monthly_impact": float(net_delta),
        },
        "cache": {
            "hits": cache_stats.hits,
            "api_lookups": cache_stats.api_lookups,
        },
        "policy": {
            "max_increase": max_increase,
            "passed": passed,
            "verdict": verdict_message,
        },
        "unpriced_count": unpriced_count,
        "zero_deltas_hidden": zero_deltas_hidden,
    }
    print(json_mod.dumps(output, indent=2))
