# CostGuard Implementation Checklist

## MUST-HAVE
- [x] 1. Accept Terraform plan JSON via stdin AND via `--plan FILE`.
- [x] 2. Parse `resource_changes[]`; handle `create`, `delete`, `update`, `replace` (`["delete","create"]`, `["create","delete"]`). Skip `no-op` and `read`.
- [x] 3. Extract SKU and Region (normalized: lowercase, no spaces).
- [x] 4. Query live Azure Retail Prices API with OData `$filter`. Follow `NextPageLink` pagination. No authentication.
- [x] 5. Embedded SQLite write-through cache in `pricing_cache.db`. Schema perfectly matches the spec. Offline hits work.
- [x] 6. Filter Spot, Low Priority, and Windows meters appropriately per OS and `priority` flag.
- [x] 7. Negative deltas: deletes show negative deltas (savings).
- [x] 8. Non-billable resources skipped cleanly with no crash or fabricated cost.
- [x] 9. Terminal table with Old, New, Delta monthly columns (Monthly = hourly x 730). Handled Disk monthly prices explicitly.
- [x] 10. `--max-increase N`: exit 0 if `<= N`, exit 1 if `> N`.

## ACCOUNTING RULES
- [x] Math logic matches: Create (0, New, +New), Delete (Old, 0, -Old), Update (Old, New, New-Old).
- [x] Handle unit explicitly (multiply by 730 for hourly, keep as-is for monthly disks).
- [x] Metadata-only updates yield exactly $0.00 delta.
- [x] Use `Decimal` for robust arithmetic.

## RESOURCE TYPES TO PRICE
- [x] `azurerm_linux_virtual_machine`
- [x] `azurerm_windows_virtual_machine`
- [x] `azurerm_virtual_machine` (legacy)
- [x] `azurerm_managed_disk` (mapped to Premium SSD tiers like P10)

## ROBUSTNESS
- [x] Unknown SKU fallback: warn and continue.
- [x] Network failure/timeout fallback: offline cache hits work, cache misses yield a clean warning and default to $0.00.
- [x] Values unknown at plan time handled gracefully.
- [x] Invalid JSON/missing input: clean error and exit 2. No traceback.
- [x] Wrap top-level in exception handler.

## STRETCH GOALS
- [x] `--currency {USD,EUR,GBP,INR}` supported.
- [x] `--clear-cache` flag implemented.
- [x] Tag-based attribution: extracted and stored in CostDelta (stretch reporting logic not fully rendered by default, but extracted).
- [x] `--markdown`: GitHub PR-comment formatted table output.
- [x] `--json`: Machine-readable JSON output.

## TESTS & DOCS
- [x] Test plans authored (Plan A, B, C, D, E).
- [x] Pytest suite passes 100%.
- [x] `README.md` created.
- [x] `REPORT.md` created.
