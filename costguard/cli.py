"""CostGuard CLI entry point.

Usage:
    terraform show -json tfplan.binary | costguard --max-increase 50
    costguard --plan plan.json --max-increase 50
    costguard --clear-cache
"""
from __future__ import annotations

import argparse
import json
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')

from costguard import __version__


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="costguard",
        description="CostGuard — Azure Infrastructure Cost Impact Analyzer for Terraform Plans",
        epilog="Exit codes: 0 = within budget, 1 = budget breached, 2 = invalid input/error",
    )
    parser.add_argument(
        "--plan",
        metavar="FILE",
        help="Path to Terraform plan JSON file. If omitted, reads from stdin.",
    )
    parser.add_argument(
        "--max-increase",
        type=float,
        metavar="N",
        help="Maximum allowed monthly cost increase (USD). Exit 1 if exceeded.",
    )
    parser.add_argument(
        "--currency",
        choices=["USD", "EUR", "GBP", "INR"],
        default="USD",
        help="Currency code for pricing (default: USD).",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Purge the SQLite pricing cache and exit.",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Output as GitHub PR comment formatted markdown.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as machine-readable JSON.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"costguard {__version__}",
    )
    return parser.parse_args(argv)


def _read_plan(args: argparse.Namespace) -> dict:
    """Read and parse the Terraform plan JSON from file or stdin.

    Returns the parsed plan dict.
    Exits with code 2 on errors.
    """
    raw = None

    if args.plan:
        try:
            with open(args.plan, "r", encoding="utf-8") as f:
                raw = f.read()
        except FileNotFoundError:
            print(f"Error: Plan file not found: {args.plan}", file=sys.stderr)
            sys.exit(2)
        except OSError as e:
            print(f"Error: Cannot read plan file: {e}", file=sys.stderr)
            sys.exit(2)
    else:
        # Read from stdin
        if sys.stdin.isatty():
            print(
                "Error: No plan provided. Use --plan FILE or pipe JSON via stdin.\n"
                "       Example: terraform show -json tfplan | costguard --max-increase 50",
                file=sys.stderr,
            )
            sys.exit(2)
        try:
            raw = sys.stdin.read().lstrip('\ufeff')
        except Exception as e:
            print(f"Error: Cannot read stdin: {e}", file=sys.stderr)
            sys.exit(2)

    if not raw or not raw.strip():
        print("Error: Empty input. Provide a valid Terraform plan JSON.", file=sys.stderr)
        sys.exit(2)

    try:
        plan_data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON input: {e}", file=sys.stderr)
        sys.exit(2)

    if not isinstance(plan_data, dict):
        print("Error: Plan JSON must be a JSON object.", file=sys.stderr)
        sys.exit(2)

    return plan_data


def main(argv: list[str] | None = None) -> None:
    """Main entry point for the CostGuard CLI."""
    try:
        args = _parse_args(argv)

        # Import heavy modules only after arg parsing for fast --help / --version
        from costguard.cache import PricingCache
        from costguard.extractor import extract_changes
        from costguard.pricing import PricingClient
        from costguard.delta import compute_deltas
        from costguard.policy import evaluate_policy
        from costguard.render import render_report

        # Handle --clear-cache
        if args.clear_cache:
            cache = PricingCache()
            cache.clear()
            print("Cache cleared successfully.")
            cache.close()
            sys.exit(0)

        # Read and parse the plan
        plan_data = _read_plan(args)

        # Extract resource changes
        changes = extract_changes(plan_data)

        # Initialize pricing with cache
        cache = PricingCache()
        pricing = PricingClient(cache, currency=args.currency)

        # Compute cost deltas
        pricing_start = time.monotonic()
        deltas = compute_deltas(changes, pricing)
        pricing_elapsed = time.monotonic() - pricing_start

        # Filter out zero-delta items for cleaner output (but keep them if they exist)
        # Actually, show all items including $0 for transparency
        # The spec says metadata-only may be omitted OR shown with $0.00
        # We'll show non-zero and omit zero-delta for cleaner output
        display_deltas = [d for d in deltas if d.delta != 0]

        # If all deltas are zero, still show something
        if not display_deltas and deltas:
            display_deltas = deltas  # Show them all with $0.00

        # Calculate net delta from ALL deltas (including zero ones)
        from decimal import Decimal
        net_delta = sum(d.delta for d in deltas) if deltas else Decimal("0")

        # Evaluate policy
        passed, verdict_message = evaluate_policy(net_delta, args.max_increase)

        # Render the report
        render_report(
            deltas=display_deltas,
            cache_stats=cache.stats,
            max_increase=args.max_increase,
            passed=passed,
            verdict_message=verdict_message,
            currency=args.currency,
            use_markdown=args.markdown,
            use_json=args.json,
        )

        # Print pricing timing to stderr (diagnostic)
        if pricing_elapsed < 0.05:
            timing_msg = f"(Pricing completed in {pricing_elapsed*1000:.0f}ms)"
        else:
            timing_msg = f"(Pricing completed in {pricing_elapsed:.2f}s)"
        print(timing_msg, file=sys.stderr)

        cache.close()

        # Exit code
        if not passed:
            sys.exit(1)
        sys.exit(0)

    except SystemExit:
        raise  # Let sys.exit() through
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"Error: Unexpected error: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
