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
        variant_key = f"{sku}|spot" if is_spot else (f"{sku}|windows" if is_windows else f"{sku}|linux")
        cache_key = (variant_key, region, self.currency)
        
        # Check session cache first (handles multiple resources with same SKU)
        if cache_key in self._session_cache:
            if self._session_cache[cache_key] is not None:
                self.cache.stats.hits += 1
            return self._session_cache[cache_key]

        # Check SQLite cache
        cached_rate = self.cache.get(variant_key, region, self.currency)
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
        
        safe_sku = sku.replace("'", "''")
        safe_region = region.replace("'", "''")
        
        filter_str = (
            f"serviceName eq 'Virtual Machines' "
            f"and armRegionName eq '{safe_region}' "
            f"and armSkuName eq '{safe_sku}' "
            f"and priceType eq 'Consumption'"
        )

        try:
            items = self._query_api(filter_str)
        except Exception as e:
            print(f"[WARN] Network unavailable; defaulting SKU '{sku}' to $0.00.", file=sys.stderr)
            result = PriceResult(rate=Decimal("0"), is_monthly=False, sku_label=sku)
            self._session_cache[cache_key] = result
            return result

        # Filter items
        matched = self._filter_vm_items(items, sku, is_windows=is_windows, is_spot=is_spot)

        if not matched:
            print(
                f"[WARN] SKU '{sku}' not found in Azure Retail API. Skipping.",
                file=sys.stderr,
            )
            self._session_cache[cache_key] = None
            return None

        # Pick the best match
        item = matched[0]
        
        if item.get("currencyCode") != self.currency:
            print(f"[WARN] Resource '{sku}' skipped: API returned wrong currency ({item.get('currencyCode')} != {self.currency})", file=sys.stderr)
            self._session_cache[cache_key] = None
            return None

        rate = Decimal(str(item["retailPrice"]))
        is_monthly = "month" in item.get("unitOfMeasure", "").lower()

        # Cache it
        self.cache.put(variant_key, region, float(rate), self.currency)

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

        # Cache key for disks uses the tier + redundancy as variant key
        variant_key = f"{tier}|{redundancy}"
        cache_key = (variant_key, region, self.currency)
        
        if cache_key in self._session_cache:
            if self._session_cache[cache_key] is not None:
                self.cache.stats.hits += 1
            return self._session_cache[cache_key]

        # Check SQLite cache
        cached_rate = self.cache.get(variant_key, region, self.currency)
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
        
        safe_region = region.replace("'", "''")
        safe_product = product_name.replace("'", "''")
        
        filter_str = (
            f"serviceName eq 'Storage' "
            f"and armRegionName eq '{safe_region}' "
            f"and priceType eq 'Consumption' "
            f"and productName eq '{safe_product}'"
        )

        try:
            items = self._query_api(filter_str)
        except Exception as e:
            print(f"[WARN] Network unavailable; defaulting disk '{tier}' to $0.00.", file=sys.stderr)
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
        
        if item.get("currencyCode") != self.currency:
            print(f"[WARN] Resource '{tier}' skipped: API returned wrong currency ({item.get('currencyCode')} != {self.currency})", file=sys.stderr)
            self._session_cache[cache_key] = None
            return None
            
        rate = Decimal(str(item["retailPrice"]))

        # Cache it
        self.cache.put(variant_key, region, float(rate), self.currency)

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
        import urllib.error
        import time
        params = {"$filter": filter_str}
        if self.currency != "USD":
            # Pass as 'EUR' with literal single quotes inside the query value string
            params["currencyCode"] = f"'{self.currency}'"

        url = API_BASE + "?" + urllib.parse.urlencode(params, safe="'")
        all_items = []
        retries = 0

        while url:
            req = urllib.request.Request(url)
            req.add_header("Accept", "application/json")
            try:
                resp = urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT)
                try:
                    data = json.loads(resp.read().decode("utf-8"))
                except json.JSONDecodeError as e:
                    raise ValueError(f"JSON decode error: {e}")
                
                all_items.extend(data.get("Items", []))
                url = data.get("NextPageLink")
                retries = 0 # reset on success
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    if retries >= 3:
                        raise ValueError(f"HTTP 429: Rate limit exceeded after {retries} retries")
                    retry_after = e.headers.get("Retry-After")
                    wait_time = int(retry_after) if retry_after and retry_after.isdigit() else (2 ** retries)
                    time.sleep(wait_time)
                    retries += 1
                    continue
                raise ValueError(f"HTTP {e.code}: {e.reason}")
            except urllib.error.URLError as e:
                raise ConnectionError(f"Network unavailable: {e.reason}")

        return all_items

    @staticmethod
    def _filter_vm_items(
        items: list[dict], sku: str, is_windows: bool = False, is_spot: bool = False
    ) -> list[dict]:
        """Filter VM pricing items to find the correct meter."""
        filtered = []
        for item in items:
            meter_name = item.get("meterName", "")
            product_name = item.get("productName", "")
            meter_lower = meter_name.lower()
            product_lower = product_name.lower()

            if "low priority" in meter_lower:
                continue

            # If spot is requested, the meter name MUST contain "spot"
            if is_spot:
                if "spot" not in meter_lower:
                    continue
            else:
                if "spot" in meter_lower:
                    continue

            # OS filtering
            if is_windows:
                if "windows" not in product_lower:
                    continue
            else:
                if "windows" in product_lower:
                    continue

            filtered.append(item)
            
        if is_spot and not filtered:
            print(f"[WARN] Spot pricing requested for SKU '{sku}' but no Spot meter exists. Unpriced.", file=sys.stderr)
            return []

        # Sort remaining candidates deterministically:
        # unitOfMeasure contains "Hour", then lowest tierMinimumUnits, then meterName
        def sort_key(i):
            uom = i.get("unitOfMeasure", "")
            has_hour = 0 if "hour" in uom.lower() else 1
            min_units = float(i.get("tierMinimumUnits", 0.0))
            m_name = i.get("meterName", "")
            return (has_hour, min_units, m_name)

        filtered.sort(key=sort_key)
        return filtered
