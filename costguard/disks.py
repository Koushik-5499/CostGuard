"""Managed disk tier mapping and SKU resolution."""
from __future__ import annotations

import sys

# Premium SSD tier thresholds: disk_size_gb -> tier name
# The size is rounded UP to the next tier boundary.
PREMIUM_SSD_TIERS: list[tuple[int, str]] = [
    (4, "P1"),
    (8, "P2"),
    (16, "P3"),
    (32, "P4"),
    (64, "P6"),
    (128, "P10"),
    (256, "P15"),
    (512, "P20"),
    (1024, "P30"),
    (2048, "P40"),
    (4096, "P50"),
    (8192, "P60"),
    (16384, "P70"),
    (32767, "P80"),
]

# Supported storage account type prefixes to service/product mapping
STORAGE_TYPE_PREFIX_MAP = {
    "Premium_LRS": ("Premium SSD Managed Disks", "LRS"),
    "Premium_ZRS": ("Premium SSD Managed Disks", "ZRS"),
    "StandardSSD_LRS": ("Standard SSD Managed Disks", "LRS"),
    "StandardSSD_ZRS": ("Standard SSD Managed Disks", "ZRS"),
    "Standard_LRS": ("Standard HDD Managed Disks", "LRS"),
    # Only Premium SSD is fully implemented per spec; others can be added
}


def resolve_disk_tier(storage_account_type: str | None, disk_size_gb: int | None) -> str | None:
    """Resolve a managed disk's tier name (e.g. 'P10') from storage type and size.

    Returns None if the tier cannot be determined (caller should warn and skip).
    """
    if not storage_account_type or disk_size_gb is None:
        return None

    # Only Premium SSD tiers are mapped per the spec
    if not storage_account_type.startswith("Premium"):
        print(
            f"[WARN] Unsupported disk storage type '{storage_account_type}'. Skipping.",
            file=sys.stderr,
        )
        return None

    # Round up to the next tier
    for max_size, tier in PREMIUM_SSD_TIERS:
        if disk_size_gb <= max_size:
            return tier

    # Size exceeds all tiers
    print(
        f"[WARN] Disk size {disk_size_gb} GB exceeds maximum supported tier. Skipping.",
        file=sys.stderr,
    )
    return None


def get_disk_product_name(storage_account_type: str | None) -> str | None:
    """Get the Azure product name for a storage account type."""
    if not storage_account_type:
        return None
    info = STORAGE_TYPE_PREFIX_MAP.get(storage_account_type)
    if info:
        return info[0]
    return None


def get_disk_redundancy(storage_account_type: str | None) -> str:
    """Get the redundancy suffix (LRS/ZRS) for a storage account type."""
    if not storage_account_type:
        return "LRS"
    info = STORAGE_TYPE_PREFIX_MAP.get(storage_account_type)
    if info:
        return info[1]
    return "LRS"


def get_disk_meter_name(tier: str, redundancy: str) -> str:
    """Build the expected meterName for a disk tier, e.g. 'P10 LRS Disk'."""
    return f"{tier} {redundancy} Disk"


def format_disk_label(tier: str, storage_type: str | None, size_gb: int | None) -> str:
    """Format a human-readable label for a disk, e.g. 'P10 Premium SSD (128GB)'."""
    parts = [tier]
    if storage_type and "Premium" in storage_type:
        parts.append("Premium SSD")
    elif storage_type and "StandardSSD" in storage_type:
        parts.append("Standard SSD")
    elif storage_type and "Standard" in storage_type:
        parts.append("Standard HDD")
    if size_gb is not None:
        parts.append(f"({size_gb}GB)")
    return " ".join(parts)
