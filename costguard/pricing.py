"""Azure Retail Prices API client with filtering and caching."""
from __future__ import annotations

import sys
import urllib.parse
import urllib.request
import json
from dataclasses import dataclass
from decimal import Decimal

from costguard.cache import PricingCache
from costguard.disks import (
    get_disk_meter_name,
    get_disk_product_name,
    get_disk_redundancy,
    resolve_disk_tier,
)

API_BASE = "https://prices.azure.com/api/retail/prices"
REQUEST_TIMEOUT = 10  # seconds


@dataclass
class PriceResult:
    """Result of a pricing lookup."""
    rate: Decimal          # The price value (hourly for VMs, monthly for disks)
    is_monthly: bool       # True if rate is already monthly (unitOfMeasure = '1/Month')
    meter_name: str = ""
    product_name: str = ""
    sku_label: str = ""    # Human-readable label for the table

    @property
    def monthly_cost(self) -> Decimal:
        """Compute monthly cost: hourly * 730, or as-is for monthly meters."""
        if self.is_monthly:
            return self.rate
        return self.rate * 730


class PricingClient:
    """Fetches prices from Azure Retail Prices API with SQLite caching.

    All prices are fetched live from the API on cache miss.
    Spot/Low Priority/Windows filtering is applied per the spec.
    """

    def __init__(self, cache: PricingCache, currency: str = "USD"):
        self.cache = cache
        self.currency = currency
        # Track SKUs we've already looked up this run to avoid duplicate API calls
        self._session_cache: dict[tuple[str, str, str], PriceResult | None] = {}

    def get_vm_price(
        self, sku: str, region: str, is_windows: bool = False, is_spot: bool = False
    ) -> PriceResult | None:
        """Get the price for a VM SKU in a region.

        Filters out Spot, Low Priority, and wrong OS meters.
        """
        cache_key = (sku, region, self.currency)
        # Check session cache first (handles multiple resources with same SKU)
        if cache_key in self._session_cache:
            return self._session_cache[cache_key]

        # Check SQLite cache
        cached_rate = self.cache.get(sku, region, self.currency)
        if cached_rate is not None:
            result = PriceResult(
                rate=Decimal(str(cached_rate)),
                is_monthly=False,
                sku_label=sku,
            )
            self._session_cache[cache_key] = result
            return result

        # Cache miss — query the API
        self.cache.stats.api_lookups += 1
        filter_str = (
            f"serviceName eq 'Virtual Machines' "
            f"and armRegionName eq '{region}' "
            f"and armSkuName eq '{sku}' "
            f"and priceType eq 'Consumption'"
        )

        try:
            items = self._query_api(filter_str)
        except Exception as e:
            print(
                f"[WARN] Network unavailable; defaulting SKU '{sku}' to $0.00.",
                file=sys.stderr,
            )
            result = PriceResult(rate=Decimal("0"), is_monthly=False, sku_label=sku)
            self._session_cache[cache_key] = result
            return result

        # Filter items
        matched = self._filter_vm_items(items, is_windows=is_windows, is_spot=is_spot)

        if not matched:
            print(
                f"[WARN] SKU '{sku}' not found in Azure Retail API. Skipping.",
                file=sys.stderr,
            )
            self._session_cache[cache_key] = None
            return None

        # Pick the best match (first remaining after filtering)
        item = matched[0]
        rate = Decimal(str(item["retailPrice"]))
        is_monthly = "month" in item.get("unitOfMeasure", "").lower()

        # Cache it
        self.cache.put(sku, region, float(rate), self.currency)

        result = PriceResult(
            rate=rate,
            is_monthly=is_monthly,
            meter_name=item.get("meterName", ""),
            product_name=item.get("productName", ""),
            sku_label=sku,
        )
        self._session_cache[cache_key] = result
        return result

    def get_disk_price(
        self,
        storage_account_type: str,
        disk_size_gb: int,
        region: str,
    ) -> PriceResult | None:
        """Get the price for a managed disk based on storage type and size.

        Resolves the tier (e.g. P10 for 128GB Premium_LRS), then looks up the price.
        """
        tier = resolve_disk_tier(storage_account_type, disk_size_gb)
        if not tier:
            return None

        redundancy = get_disk_redundancy(storage_account_type)
        product_name = get_disk_product_name(storage_account_type)
        if not product_name:
            return None

        # Cache key for disks uses the tier as SKU
        cache_key = (tier, region, self.currency)
        if cache_key in self._session_cache:
            return self._session_cache[cache_key]

        # Check SQLite cache
        cached_rate = self.cache.get(tier, region, self.currency)
        if cached_rate is not None:
            from costguard.disks import format_disk_label
            result = PriceResult(
                rate=Decimal(str(cached_rate)),
                is_monthly=True,  # Disk prices are always monthly
                sku_label=format_disk_label(tier, storage_account_type, disk_size_gb),
            )
            self._session_cache[cache_key] = result
            return result

        # Cache miss — query the API
        self.cache.stats.api_lookups += 1
        filter_str = (
            f"serviceName eq 'Storage' "
            f"and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption' "
            f"and productName eq '{product_name}'"
        )

        try:
            items = self._query_api(filter_str)
        except Exception as e:
            print(
                f"[WARN] Network unavailable; defaulting disk '{tier}' to $0.00.",
                file=sys.stderr,
            )
            result = PriceResult(rate=Decimal("0"), is_monthly=True, sku_label=tier)
            self._session_cache[cache_key] = result
            return result

        # Find the matching tier: look for the "P10 LRS Disk" meterName pattern
        expected_meter = get_disk_meter_name(tier, redundancy)
        matched = [
            item for item in items
            if item.get("meterName", "") == expected_meter
        ]

        if not matched:
            print(
                f"[WARN] Disk tier '{tier}' ({expected_meter}) not found in Azure Retail API. Skipping.",
                file=sys.stderr,
            )
            self._session_cache[cache_key] = None
            return None

        item = matched[0]
        rate = Decimal(str(item["retailPrice"]))

        # Cache it
        self.cache.put(tier, region, float(rate), self.currency)

        from costguard.disks import format_disk_label
        result = PriceResult(
            rate=rate,
            is_monthly=True,  # Disk prices are "1/Month"
            meter_name=item.get("meterName", ""),
            product_name=item.get("productName", ""),
            sku_label=format_disk_label(tier, storage_account_type, disk_size_gb),
        )
        self._session_cache[cache_key] = result
        return result

    def _query_api(self, filter_str: str) -> list[dict]:
        """Query the Azure Retail Prices API with pagination support."""
        params = {"$filter": filter_str}
        if self.currency != "USD":
            params["currencyCode"] = self.currency

        url = API_BASE + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        all_items = []

        while url:
            req = urllib.request.Request(url)
            req.add_header("Accept", "application/json")
            resp = urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT)
            data = json.loads(resp.read().decode("utf-8"))
            all_items.extend(data.get("Items", []))
            url = data.get("NextPageLink")

        return all_items

    @staticmethod
    def _filter_vm_items(
        items: list[dict], is_windows: bool = False, is_spot: bool = False
    ) -> list[dict]:
        """Filter VM pricing items to find the correct meter.

        Rules:
        - Reject Spot meters (meterName contains "Spot") unless is_spot is True.
        - Reject Low Priority meters (meterName contains "Low Priority") always.
        - For Linux VMs: reject Windows meters (productName contains "Windows").
        - For Windows VMs: only keep Windows meters.
        """
        filtered = []
        for item in items:
            meter_name = item.get("meterName", "")
            product_name = item.get("productName", "")
            meter_lower = meter_name.lower()
            product_lower = product_name.lower()

            # Always reject Low Priority
            if "low priority" in meter_lower:
                continue

            # Reject Spot unless the plan says Spot
            if "spot" in meter_lower and not is_spot:
                continue

            # OS filtering
            if is_windows:
                # For Windows VMs, only keep Windows meters
                if "windows" not in product_lower:
                    continue
            else:
                # For Linux VMs, exclude Windows meters
                if "windows" in product_lower:
                    continue

            filtered.append(item)

        return filtered
