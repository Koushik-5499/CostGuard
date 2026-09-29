# CostGuard Implementation Report

## 1) What We Built
CostGuard is a Python CLI designed to intercept Terraform plan JSON files and calculate exact monthly cost deltas natively. It queries the live Azure Retail Prices API for dynamic pricing using HTTP filtering (Spot/Windows exclusions) without requiring user authentication. It implements a local SQLite read/write-through cache. The core extraction engine parses create, delete, update, and replace configurations. Missing or hostile resources fallback without traceback crashes.

## 2) Detection & Extraction Logic
- **Parsing Engine:** Maps `resource_changes[]` to identify operations (`create`, `delete`, `update`, `replace` — where replacement is handled by recognizing `["delete", "create"]` actions).
- **VM Mapping:** Targets `azurerm_linux_virtual_machine`, `azurerm_windows_virtual_machine`. Evaluates the correct `size` and region (normalization strips spaces to lowercase, e.g., `eastus`).
- **Disk Mapping:** Targets `azurerm_managed_disk`. The code maps sizes to standard tier IDs (e.g., `< 4GB` = `P1`, `4-128GB` = `P10`) natively using `storage_account_type`.

## 3) Methods Table

| Feature | Implementation Strategy |
|:---|:---|
| **Pricing Engine** | Live query via `pricing.py` to `https://prices.azure.com/api/retail/prices` using precise OData `$filter` syntax. Pagination is handled with a `while url:` loop via `NextPageLink`. Request timeout set to 10s securely. |
| **Caching Strategy** | Local `pricing_cache.db` (SQLite). Schema follows `(sku, region, currency)` PRIMARY KEY, tracking `hourly_rate` (also holds flat rates for monthly disks) and `cached_at`. |
| **Spot & Low Priority**| `_filter_vm_items()` parses returned payloads. "Low priority" is discarded entirely. "Spot" is filtered out unless `priority="Spot"` is explicitly requested. |
| **OS Meter Filtering** | Linux VM requests natively exclude meter products containing the string "Windows". |
| **Monthly math** | VM rates are pulled hourly, multiplied by `730` dynamically in `delta.py`. Managed disks natively fetch `1/Month` prices; the engine maps `is_monthly=True` and ignores the 730 multiplier. |

## 4) Results Matrix

Based on actual PowerShell terminal execution runs in this session:

### Plan A (Create)
**Command:**
```powershell
costguard --plan test-plans/plan_a_small_add.json --max-increase 50
```
**Actual output:**
```text
(Pricing completed in 0ms)
====================================================================================================
COSTGUARD: Azure Infrastructure Cost Impact Report
====================================================================================================
Resource Address                       Action   Region     SKU / Meter               Old ($/mo) New ($/mo) Delta ($/mo)
----------------------------------------------------------------------------------------------------
azurerm_linux_virtual_machine.app      CREATE   eastus     Standard_B1s                   $0.00      $7.59       +$7.59
azurerm_managed_disk.data              CREATE   eastus     P10 Premium SSD (128GB)        $0.00     $19.71      +$19.71
----------------------------------------------------------------------------------------------------
FINANCIAL SUMMARY:
  Prior Monthly Total: $0.00/mo
  Projected Monthly Total: $27.30/mo
  Net Monthly Impact: +$27.30/mo   [Cache: 2 hits, 0 API lookups]
----------------------------------------------------------------------------------------------------
POLICY VERDICT:
  Budget Threshold: +$50.00/mo
  Status: PASSED (Within budget allowance)
====================================================================================================
exit=0
```
**Exit code:** `0`

### Plan B (Upgrade/Delete)
**Command:**
```powershell
Get-Content test-plans/plan_b_upgrade_delete.json -Raw | costguard --max-increase 25
```
**Actual output:**
```text
(Pricing completed in 2.36s)
====================================================================================================
COSTGUARD: Azure Infrastructure Cost Impact Report
====================================================================================================
Resource Address                       Action   Region     SKU / Meter               Old ($/mo) New ($/mo) Delta ($/mo)
----------------------------------------------------------------------------------------------------
azurerm_linux_virtual_machine.web      UPDATE   eastus     Standard_B2s -> Standard_D2s_v3     $30.37     $70.08      +$39.71
azurerm_linux_virtual_machine.old      DELETE   eastus     Standard_B1s                   $7.59      $0.00       -$7.59
----------------------------------------------------------------------------------------------------
FINANCIAL SUMMARY:
  Prior Monthly Total: $37.96/mo
  Projected Monthly Total: $70.08/mo
  Net Monthly Impact: +$32.12/mo   [Cache: 1 hits, 2 API lookups]
----------------------------------------------------------------------------------------------------
POLICY VERDICT:
  Budget Threshold: +$25.00/mo
  Status: FAILED (Exceeds budget allowance by +$7.12/mo)

[CIRCUIT BREAKER] CostGuard: Budget threshold breached. Deployment blocked.
====================================================================================================
```
**Exit code:** `1`

### Plan C (Hostile Noise)
**Command:**
```powershell
costguard --plan test-plans/plan_c_hostile_noise.json --max-increase 10
```
**Actual output:**
```text
(Pricing completed in 0ms)
====================================================================================================
COSTGUARD: Azure Infrastructure Cost Impact Report
====================================================================================================
Resource Address                       Action   Region     SKU / Meter               Old ($/mo) New ($/mo) Delta ($/mo)
----------------------------------------------------------------------------------------------------
----------------------------------------------------------------------------------------------------
FINANCIAL SUMMARY:
  Prior Monthly Total: $0.00/mo
  Projected Monthly Total: $0.00/mo
  Net Monthly Impact: $0.00/mo   [Cache: 0 hits, 0 API lookups]
----------------------------------------------------------------------------------------------------
POLICY VERDICT:
  Budget Threshold: +$10.00/mo
  Status: PASSED (Within budget allowance)
====================================================================================================
```
**Exit code:** `0`

### Plan E (Corrupt JSON)
**Command:**
```powershell
costguard --plan test-plans/plan_e_corrupt.json
```
**Actual output:**
```text
Error: Invalid JSON input: Expecting ',' delimiter: line 10 column 7 (char 248)
```
**Exit code:** `2`


## 5) Limitations
- **Tag Grouping:** Terminal tag aggregation is not implemented. Tags are extracted into the object model and `--json` structural support exists, but it remains a partial capability as visual aggregation is not supported.
- **Disk Mappings:** Managed disk processing natively supports Premium SSD classes. It does not encompass automated logic for Standard HDD, Standard SSD, or Ultra disk tiers.
- **Single Cloud:** The application works entirely against Azure Retail API constraints securely; multi-cloud compatibility is not integrated.
- **Egress:** Traffic and explicit networking volume usage metrics are skipped.

## 6) How to Run It

To execute the tool identically to how it was verified in this session:

```powershell
# 1. Install the tool globally to your active Python runtime
pip install -e .

# 2. Add python scripts to path (Crucial step for Windows users!)
$env:PATH += ";C:\Users\user\AppData\Local\Python\pythoncore-3.14-64\Scripts"

# 3. Test functional CLI bounds
costguard --plan test-plans/plan_a_small_add.json --max-increase 50
costguard --plan test-plans/plan_b_upgrade_delete.json --max-increase 25

# 4. View stretch functionalities 
costguard --plan test-plans/plan_a_small_add.json --markdown
costguard --plan test-plans/plan_a_small_add.json --json
costguard --plan test-plans/plan_a_small_add.json --currency EUR

# 5. Clear Cache explicitly
costguard --clear-cache
```
