"""Delta computation: calculate old/new/delta monthly costs for each resource change."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import sys
from costguard.extractor import ResourceChange, VM_TYPES, DISK_TYPES
from costguard.pricing import PriceResult, PricingClient


@dataclass
class CostDelta:
    """Cost delta for a single resource."""
    address: str
    action: str           # CREATE, DELETE, UPDATE, REPLACE
    region: str
    sku_label: str        # Human-readable SKU/meter label
    old_monthly: Decimal  # Monthly cost before
    new_monthly: Decimal  # Monthly cost after
    delta: Decimal        # new - old
    tags: dict[str, str]
    unpriced: bool = False


def compute_action_label(actions: list[str]) -> str:
    """Convert Terraform actions list to a human-readable label."""
    if actions == ["create"]:
        return "CREATE"
    elif actions == ["delete"]:
        return "DELETE"
    elif actions == ["update"]:
        return "UPDATE"
    elif actions in [["delete", "create"], ["create", "delete"]]:
        return "REPLACE"
    return "UNKNOWN"


def compute_deltas(
    changes: list[ResourceChange],
    pricing: PricingClient,
) -> list[CostDelta]:
    """Compute cost deltas for all resource changes.

    For each resource:
    - CREATE: old=0, new=price, delta=+price
    - DELETE: old=price, new=0, delta=-price
    - UPDATE: old=before_price, new=after_price, delta=new-old
    - REPLACE: old=destroyed_price, new=created_price, delta=new-old

    Metadata-only updates (same SKU/size/disk config) produce $0.00 delta.
    """
    results: list[CostDelta] = []

    for change in changes:
        action = compute_action_label(change.actions)

        if change.resource_type in VM_TYPES:
            delta = _compute_vm_delta(change, action, pricing)
        elif change.resource_type in DISK_TYPES:
            delta = _compute_disk_delta(change, action, pricing)
        else:
            continue

        if delta is not None:
            results.append(delta)

    return results


def _get_vm_price(
    pricing: PricingClient, sku: str | None, region: str | None,
    is_windows: bool, is_spot: bool
) -> PriceResult | None:
    """Lookup VM price, returning None if SKU or region is missing."""
    if not sku or not region:
        return None
    return pricing.get_vm_price(sku, region, is_windows=is_windows, is_spot=is_spot)


def _compute_vm_delta(
    change: ResourceChange, action: str, pricing: PricingClient
) -> CostDelta | None:
    """Compute cost delta for a VM resource change."""
    old_monthly = Decimal("0")
    new_monthly = Decimal("0")
    sku_label_parts = []
    region = change.region_after or change.region_before or "unknown"

    if action not in ["CREATE", "DELETE", "UPDATE", "REPLACE"]:
        print(f"[WARN] Resource {change.address} skipped: unsupported action {change.actions}", file=sys.stderr)
        return None

    if action == "CREATE":
        price = _get_vm_price(
            pricing, change.sku_after, change.region_after,
            change.is_windows, change.is_spot
        )
        if price is None:
            return CostDelta(
                address=change.address,
                action=action,
                region=region,
                sku_label=change.sku_after or "?",
                old_monthly=Decimal("0"),
                new_monthly=Decimal("0"),
                delta=Decimal("0"),
                tags=change.tags_after,
                unpriced=True
            )
        new_monthly = price.monthly_cost
        sku_label_parts.append(change.sku_after or "?")

    elif action == "DELETE":
        price = _get_vm_price(
            pricing, change.sku_before, change.region_before,
            change.is_windows, change.is_spot
        )
        if price is None:
            return CostDelta(
                address=change.address,
                action=action,
                region=region,
                sku_label=change.sku_before or "?",
                old_monthly=Decimal("0"),
                new_monthly=Decimal("0"),
                delta=Decimal("0"),
                tags=change.tags_after,
                unpriced=True
            )
        old_monthly = price.monthly_cost
        sku_label_parts.append(change.sku_before or "?")

    elif action == "UPDATE":
        # Check if this is a metadata-only update (same SKU and same region)
        same_sku = change.sku_before == change.sku_after
        same_region = change.region_before == change.region_after
        if same_sku and (same_region or change.region_before is None or change.region_after is None):
            # Same SKU and Region — $0.00 delta (tags/metadata change)
            sku_label_parts.append(change.sku_after or change.sku_before or "?")
            price_before = _get_vm_price(
                pricing, change.sku_before, change.region_before or change.region_after,
                change.is_windows, change.is_spot
            )
            if price_before is None:
                return CostDelta(
                    address=change.address,
                    action=action,
                    region=region,
                    sku_label=" ".join(sku_label_parts),
                    old_monthly=Decimal("0"),
                    new_monthly=Decimal("0"),
                    delta=Decimal("0"),
                    tags=change.tags_after,
                    unpriced=True
                )
            old_monthly = price_before.monthly_cost
            new_monthly = price_before.monthly_cost
            return CostDelta(
                address=change.address,
                action=action,
                region=region,
                sku_label=" ".join(sku_label_parts),
                old_monthly=old_monthly,
                new_monthly=new_monthly,
                delta=Decimal("0"),
                tags=change.tags_after,
            )

        # Different SKU — real upgrade/downgrade
        price_before = _get_vm_price(
            pricing, change.sku_before, change.region_before or change.region_after,
            change.is_windows, change.is_spot
        )
        price_after = _get_vm_price(
            pricing, change.sku_after, change.region_after or change.region_before,
            change.is_windows, change.is_spot
        )
        if price_before is None or price_after is None:
            return CostDelta(
                address=change.address,
                action=action,
                region=region,
                sku_label=f"{change.sku_before or '?'} -> {change.sku_after or '?'}",
                old_monthly=Decimal("0"),
                new_monthly=Decimal("0"),
                delta=Decimal("0"),
                tags=change.tags_after,
                unpriced=True
            )
        if price_before:
            old_monthly = price_before.monthly_cost
        if price_after:
            new_monthly = price_after.monthly_cost
        sku_label_parts.append(f"{change.sku_before or '?'} -> {change.sku_after or '?'}")

    elif action == "REPLACE":
        price_before = _get_vm_price(
            pricing, change.sku_before, change.region_before or change.region_after,
            change.is_windows, change.is_spot
        )
        price_after = _get_vm_price(
            pricing, change.sku_after, change.region_after or change.region_before,
            change.is_windows, change.is_spot
        )
        if price_before is None or price_after is None:
            return CostDelta(
                address=change.address,
                action=action,
                region=region,
                sku_label=f"{change.sku_before or '?'} -> {change.sku_after or '?'}",
                old_monthly=Decimal("0"),
                new_monthly=Decimal("0"),
                delta=Decimal("0"),
                tags=change.tags_after,
                unpriced=True
            )
        if price_before:
            old_monthly = price_before.monthly_cost
        if price_after:
            new_monthly = price_after.monthly_cost
        sku_label_parts.append(f"{change.sku_before or '?'} -> {change.sku_after or '?'}")

    delta = new_monthly - old_monthly
    sku_label = " ".join(sku_label_parts) if sku_label_parts else "?"

    return CostDelta(
        address=change.address,
        action=action,
        region=region,
        sku_label=sku_label,
        old_monthly=old_monthly,
        new_monthly=new_monthly,
        delta=delta,
        tags=change.tags_after,
    )


def _compute_disk_delta(
    change: ResourceChange, action: str, pricing: PricingClient
) -> CostDelta | None:
    """Compute cost delta for a managed disk resource change."""
    old_monthly = Decimal("0")
    new_monthly = Decimal("0")
    sku_label_parts = []
    region = change.region_after or change.region_before or "unknown"

    if action not in ["CREATE", "DELETE", "UPDATE", "REPLACE"]:
        print(f"[WARN] Resource {change.address} skipped: unsupported action {change.actions}", file=sys.stderr)
        return None

    if action == "CREATE":
        if change.disk_storage_type_after and change.disk_size_gb_after is not None:
            price = pricing.get_disk_price(
                change.disk_storage_type_after, change.disk_size_gb_after, region
            )
            if price is None:
                return CostDelta(
                    address=change.address,
                    action=action,
                    region=region,
                    sku_label="?",
                    old_monthly=Decimal("0"),
                    new_monthly=Decimal("0"),
                    delta=Decimal("0"),
                    tags=change.tags_after,
                    unpriced=True
                )
            new_monthly = price.monthly_cost
            sku_label_parts.append(price.sku_label)
        else:
            print(f"[WARN] Resource {change.address} skipped: missing disk size or type", file=sys.stderr)
            return None

    elif action == "DELETE":
        if change.disk_storage_type_before and change.disk_size_gb_before is not None:
            price = pricing.get_disk_price(
                change.disk_storage_type_before, change.disk_size_gb_before,
                change.region_before or region
            )
            if price is None:
                return CostDelta(
                    address=change.address,
                    action=action,
                    region=region,
                    sku_label="?",
                    old_monthly=Decimal("0"),
                    new_monthly=Decimal("0"),
                    delta=Decimal("0"),
                    tags=change.tags_after,
                    unpriced=True
                )
            old_monthly = price.monthly_cost
            sku_label_parts.append(price.sku_label)
        else:
            print(f"[WARN] Resource {change.address} skipped: missing disk size or type", file=sys.stderr)
            return None

    elif action == "UPDATE":
        # Check for metadata-only update
        same_region = change.region_before == change.region_after
        same_config = (
            change.disk_storage_type_before == change.disk_storage_type_after
            and change.disk_size_gb_before == change.disk_size_gb_after
            and (same_region or change.region_before is None or change.region_after is None)
        )
        if same_config:
            if change.disk_storage_type_after and change.disk_size_gb_after is not None:
                price = pricing.get_disk_price(
                    change.disk_storage_type_after, change.disk_size_gb_after, region
                )
                if price is None:
                    return CostDelta(
                        address=change.address,
                        action=action,
                        region=region,
                        sku_label="?",
                        old_monthly=Decimal("0"),
                        new_monthly=Decimal("0"),
                        delta=Decimal("0"),
                        tags=change.tags_after,
                        unpriced=True
                    )
                old_monthly = price.monthly_cost
                new_monthly = price.monthly_cost
                sku_label_parts.append(price.sku_label)
            else:
                return CostDelta(
                    address=change.address,
                    action=action,
                    region=region,
                    sku_label="?",
                    old_monthly=Decimal("0"),
                    new_monthly=Decimal("0"),
                    delta=Decimal("0"),
                    tags=change.tags_after,
                    unpriced=True
                )
            return CostDelta(
                address=change.address,
                action=action,
                region=region,
                sku_label=" ".join(sku_label_parts) if sku_label_parts else "?",
                old_monthly=old_monthly,
                new_monthly=new_monthly,
                delta=Decimal("0"),
                tags=change.tags_after,
            )

        # Real config change
        if change.disk_storage_type_before and change.disk_size_gb_before is not None:
            price_before = pricing.get_disk_price(
                change.disk_storage_type_before, change.disk_size_gb_before,
                change.region_before or region
            )
        else:
            price_before = None

        if change.disk_storage_type_after and change.disk_size_gb_after is not None:
            price_after = pricing.get_disk_price(
                change.disk_storage_type_after, change.disk_size_gb_after, region
            )
        else:
            price_after = None

        if price_before is None or price_after is None:
            return CostDelta(
                address=change.address,
                action=action,
                region=region,
                sku_label="?",
                old_monthly=Decimal("0"),
                new_monthly=Decimal("0"),
                delta=Decimal("0"),
                tags=change.tags_after,
                unpriced=True
            )
        
        old_monthly = price_before.monthly_cost
        new_monthly = price_after.monthly_cost
        sku_label_parts.append(price_after.sku_label)

    elif action == "REPLACE":
        if change.disk_storage_type_before and change.disk_size_gb_before is not None:
            price_before = pricing.get_disk_price(
                change.disk_storage_type_before, change.disk_size_gb_before,
                change.region_before or region
            )
        else:
            price_before = None
            
        if change.disk_storage_type_after and change.disk_size_gb_after is not None:
            price_after = pricing.get_disk_price(
                change.disk_storage_type_after, change.disk_size_gb_after, region
            )
        else:
            price_after = None

        if price_before is None or price_after is None:
            return CostDelta(
                address=change.address,
                action=action,
                region=region,
                sku_label="?",
                old_monthly=Decimal("0"),
                new_monthly=Decimal("0"),
                delta=Decimal("0"),
                tags=change.tags_after,
                unpriced=True
            )
        
        old_monthly = price_before.monthly_cost
        new_monthly = price_after.monthly_cost
        sku_label_parts.append(price_after.sku_label)

    delta = new_monthly - old_monthly
    sku_label = " ".join(sku_label_parts) if sku_label_parts else "?"

    return CostDelta(
        address=change.address,
        action=action,
        region=region,
        sku_label=sku_label,
        old_monthly=old_monthly,
        new_monthly=new_monthly,
        delta=delta,
        tags=change.tags_after,
    )
