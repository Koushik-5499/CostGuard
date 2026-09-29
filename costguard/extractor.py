"""Extractor: parse Terraform plan JSON and extract resource changes with SKU/region info."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

# Resource types we know how to price
VM_TYPES = {
    "azurerm_linux_virtual_machine",
    "azurerm_windows_virtual_machine",
    "azurerm_virtual_machine",  # legacy
}

DISK_TYPES = {
    "azurerm_managed_disk",
}

# Resource types that are never billable – skip silently
NON_BILLABLE_TYPES = {
    "azurerm_resource_group",
    "azurerm_virtual_network",
    "azurerm_subnet",
    "azurerm_network_security_group",
    "azurerm_network_security_rule",
    "azurerm_network_interface",
    "azurerm_public_ip",  # could be priced later, but spec says skip unknown
}

PRICEABLE_TYPES = VM_TYPES | DISK_TYPES


@dataclass
class ResourceChange:
    """A single resource change extracted from the Terraform plan."""
    address: str
    resource_type: str
    actions: list[str]
    region_before: str | None = None
    region_after: str | None = None
    sku_before: str | None = None
    sku_after: str | None = None
    is_windows: bool = False
    is_spot: bool = False
    # For managed disks
    disk_storage_type_before: str | None = None
    disk_storage_type_after: str | None = None
    disk_size_gb_before: int | None = None
    disk_size_gb_after: int | None = None
    # Tags for stretch goal
    tags_after: dict[str, str] = field(default_factory=dict)


def normalize_region(location: str | None) -> str | None:
    """Normalize Azure region: lowercase, no spaces. 'East US' -> 'eastus'."""
    if not location:
        return None
    return location.lower().replace(" ", "")


def _get_vm_sku(config: dict[str, Any] | None) -> str | None:
    """Extract VM SKU from a config block (before or after)."""
    if not config:
        return None
    # azurerm_linux_virtual_machine / azurerm_windows_virtual_machine use 'size'
    sku = config.get("size")
    if not sku:
        # azurerm_virtual_machine (legacy) uses 'vm_size'
        sku = config.get("vm_size")
    return sku


def _get_location(config: dict[str, Any] | None) -> str | None:
    """Extract and normalize location from a config block."""
    if not config:
        return None
    loc = config.get("location")
    return normalize_region(loc)


def _is_spot_vm(config: dict[str, Any] | None) -> bool:
    """Check if a VM config is a Spot instance."""
    if not config:
        return False
    priority = config.get("priority", "")
    if isinstance(priority, str) and priority.lower() == "spot":
        return True
    eviction_policy = config.get("eviction_policy")
    if eviction_policy is not None:
        return True
    return False


def _is_windows_type(resource_type: str) -> bool:
    """Determine if the resource type implies a Windows VM."""
    return resource_type == "azurerm_windows_virtual_machine"


def _safe_get(d: dict | None, key: str, default=None):
    """Safely get a value from a dict that might be None."""
    if d is None:
        return default
    val = d.get(key)
    return val if val is not None else default


def extract_changes(plan_data: dict[str, Any]) -> list[ResourceChange]:
    """Extract all resource changes from a Terraform plan JSON.

    Returns a list of ResourceChange objects for priceable resources.
    Non-billable and unknown resource types are silently skipped.
    """
    resource_changes = plan_data.get("resource_changes", [])
    if not resource_changes:
        return []

    results: list[ResourceChange] = []

    for rc in resource_changes:
        try:
            address = rc.get("address", "<unknown>")
            resource_type = rc.get("type", "")
            change = rc.get("change", {})
            if not change:
                continue

            actions = change.get("actions", [])
            if not actions:
                continue

            # Skip no-op and read actions
            if actions == ["no-op"] or actions == ["read"]:
                continue

            before = change.get("before") or {}
            after = change.get("after") or {}
            after_unknown = change.get("after_unknown") or {}

            # Determine if this is a priceable resource
            if resource_type not in PRICEABLE_TYPES:
                if resource_type not in NON_BILLABLE_TYPES:
                    # Unknown type - warn and skip
                    print(
                        f"[WARN] Unknown resource type '{resource_type}' for "
                        f"'{address}'. Skipping.",
                        file=sys.stderr,
                    )
                continue

            if resource_type in VM_TYPES:
                rc_obj = _extract_vm_change(
                    address, resource_type, actions, before, after, after_unknown
                )
            elif resource_type in DISK_TYPES:
                rc_obj = _extract_disk_change(
                    address, resource_type, actions, before, after, after_unknown
                )
            else:
                continue

            if rc_obj is not None:
                # Extract tags
                tags = _safe_get(after, "tags") or {}
                if isinstance(tags, dict):
                    rc_obj.tags_after = tags
                results.append(rc_obj)

        except Exception as e:
            print(
                f"[WARN] Error extracting resource '{rc.get('address', '?')}': {e}. Skipping.",
                file=sys.stderr,
            )
            continue

    return results


def _extract_vm_change(
    address: str,
    resource_type: str,
    actions: list[str],
    before: dict,
    after: dict,
    after_unknown: dict,
) -> ResourceChange | None:
    """Extract a VM resource change."""
    sku_before = _get_vm_sku(before) if before else None
    sku_after = _get_vm_sku(after) if after else None

    # Handle after_unknown for size
    if after_unknown.get("size") is True or after_unknown.get("vm_size") is True:
        if not sku_after:
            print(
                f"[WARN] VM size unknown at plan time for '{address}'. Skipping.",
                file=sys.stderr,
            )
            return None

    region_before = _get_location(before) if before else None
    region_after = _get_location(after) if after else None

    is_windows = _is_windows_type(resource_type)
    is_spot = _is_spot_vm(after) if after else _is_spot_vm(before)

    return ResourceChange(
        address=address,
        resource_type=resource_type,
        actions=actions,
        region_before=region_before,
        region_after=region_after,
        sku_before=sku_before,
        sku_after=sku_after,
        is_windows=is_windows,
        is_spot=is_spot,
    )


def _extract_disk_change(
    address: str,
    resource_type: str,
    actions: list[str],
    before: dict,
    after: dict,
    after_unknown: dict,
) -> ResourceChange | None:
    """Extract a managed disk resource change."""
    region_before = _get_location(before) if before else None
    region_after = _get_location(after) if after else None

    disk_storage_type_before = _safe_get(before, "storage_account_type")
    disk_storage_type_after = _safe_get(after, "storage_account_type")

    disk_size_before = _safe_get(before, "disk_size_gb")
    disk_size_after = _safe_get(after, "disk_size_gb")

    # Safely convert to int
    try:
        disk_size_before = int(disk_size_before) if disk_size_before is not None else None
    except (ValueError, TypeError):
        disk_size_before = None

    try:
        disk_size_after = int(disk_size_after) if disk_size_after is not None else None
    except (ValueError, TypeError):
        disk_size_after = None

    return ResourceChange(
        address=address,
        resource_type=resource_type,
        actions=actions,
        region_before=region_before,
        region_after=region_after,
        disk_storage_type_before=disk_storage_type_before,
        disk_storage_type_after=disk_storage_type_after,
        disk_size_gb_before=disk_size_before,
        disk_size_gb_after=disk_size_after,
    )
