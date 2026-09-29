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
        "--strict",
        action="store_true",
        help="Exit 2 if any billable resource cannot be priced.",
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
    raw_bytes = None

    if args.plan:
        try:
            with open(args.plan, "rb") as f:
                raw_bytes = f.read()
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
            raw_bytes = sys.stdin.buffer.read()
        except Exception as e:
            print(f"Error: Cannot read stdin: {e}", file=sys.stderr)
            sys.exit(2)

    if not raw_bytes or not raw_bytes.strip():
        print("Error: Empty input. Provide a valid Terraform plan JSON.", file=sys.stderr)
        sys.exit(2)

    if raw_bytes.startswith(b'\xff\xfe') or raw_bytes.startswith(b'\xfe\xff'):
        try:
            raw = raw_bytes.decode('utf-16')
        except UnicodeDecodeError:
            print("Error: Plan file is not valid UTF-8/UTF-16 text", file=sys.stderr)
            sys.exit(2)
    else:
        try:
            raw = raw_bytes.decode('utf-8-sig')
        except UnicodeDecodeError:
            print("Error: Plan file is not valid UTF-8/UTF-16 text", file=sys.stderr)
            sys.exit(2)

    try:
        plan_data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON input: {e}", file=sys.stderr)
        sys.exit(2)

    if not isinstance(plan_data, dict):
        print("Error: Plan JSON must be a JSON object.", file=sys.stderr)
        sys.exit(2)

    if "resource_changes" not in plan_data:
        if "values" in plan_data:
            print("Error: JSON appears to be a Terraform state file, not a plan file.", file=sys.stderr)
        else:
            print("Error: JSON is missing 'resource_changes' list.", file=sys.stderr)
        sys.exit(2)

    if not isinstance(plan_data["resource_changes"], list):
        print("Error: 'resource_changes' must be a list.", file=sys.stderr)
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
            # If a plan was supplied, continue, else exit
            if not sys.stdin.isatty() or args.plan:
                pass # continue with fresh lookups
            else:
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

        # Identify unpriced items
        unpriced_deltas = [d for d in deltas if getattr(d, 'unpriced', False)]
        if args.strict and unpriced_deltas:
            print("Error: Strict mode enabled and billable resources could not be priced:", file=sys.stderr)
            for d in unpriced_deltas:
                print(f"  - {d.address} ({d.action})", file=sys.stderr)
            cache.close()
            sys.exit(2)

        # We show non-zero and unpriced items, hide metadata-only (zero delta)
        display_deltas = [d for d in deltas if d.delta != 0 or getattr(d, 'unpriced', False)]
        zero_deltas_hidden = sum(1 for d in deltas if d.delta == 0 and not getattr(d, 'unpriced', False))

        # Calculate net delta from ALL deltas (including zero ones) and round to 2 decimals
        from decimal import Decimal, ROUND_HALF_UP
        net_delta = sum(d.delta for d in deltas) if deltas else Decimal("0")
        net_delta = net_delta.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

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
            unpriced_count=len(unpriced_deltas) if not args.strict else 0,
            zero_deltas_hidden=zero_deltas_hidden,
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
