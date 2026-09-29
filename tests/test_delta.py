from decimal import Decimal
from costguard.extractor import ResourceChange
from costguard.delta import compute_deltas
from costguard.pricing import PriceResult

class MockPricingClient:
    def __init__(self):
        self.vm_prices = {}
        self.disk_prices = {}
        
    def get_vm_price(self, sku, region, is_windows=False, is_spot=False):
        if (sku, region) in self.vm_prices:
            return PriceResult(rate=Decimal(str(self.vm_prices[(sku, region)])), is_monthly=False, sku_label=sku)
        return None
        
    def get_disk_price(self, storage_type, size, region):
        key = (storage_type, size, region)
        if key in self.disk_prices:
            return PriceResult(rate=Decimal(str(self.disk_prices[key])), is_monthly=True, sku_label=f"Disk {size}GB")
        return None

def test_compute_deltas():
    pricing = MockPricingClient()
    pricing.vm_prices[("Standard_B1s", "eastus")] = 0.0104
    pricing.vm_prices[("Standard_B2s", "eastus")] = 0.0416
    pricing.disk_prices[("Premium_LRS", 128, "eastus")] = 19.71
    
    changes = [
        # CREATE VM
        ResourceChange(
            address="vm1", resource_type="azurerm_linux_virtual_machine", actions=["create"],
            region_after="eastus", sku_after="Standard_B1s"
        ),
        # DELETE VM
        ResourceChange(
            address="vm2", resource_type="azurerm_linux_virtual_machine", actions=["delete"],
            region_before="eastus", sku_before="Standard_B2s"
        ),
        # UPDATE VM (Metadata only)
        ResourceChange(
            address="vm3", resource_type="azurerm_linux_virtual_machine", actions=["update"],
            region_before="eastus", region_after="eastus", sku_before="Standard_B1s", sku_after="Standard_B1s"
        ),
        # REPLACE VM (Upgrade)
        ResourceChange(
            address="vm4", resource_type="azurerm_linux_virtual_machine", actions=["delete", "create"],
            region_before="eastus", region_after="eastus", sku_before="Standard_B1s", sku_after="Standard_B2s"
        ),
        # CREATE DISK
        ResourceChange(
            address="disk1", resource_type="azurerm_managed_disk", actions=["create"],
            region_after="eastus", disk_storage_type_after="Premium_LRS", disk_size_gb_after=128
        )
    ]
    
    deltas = compute_deltas(changes, pricing)
    assert len(deltas) == 5
    
    # VM1 Create
    assert deltas[0].old_monthly == Decimal("0")
    assert deltas[0].new_monthly == Decimal("7.592") # 0.0104 * 730
    assert deltas[0].delta == Decimal("7.592")
    
    # VM2 Delete
    assert deltas[1].old_monthly == Decimal("30.368") # 0.0416 * 730
    assert deltas[1].new_monthly == Decimal("0")
    assert deltas[1].delta == Decimal("-30.368")
    
    # VM3 Metadata Update
    assert deltas[2].old_monthly == Decimal("7.592")
    assert deltas[2].new_monthly == Decimal("7.592")
    assert deltas[2].delta == Decimal("0")
    
    # VM4 Replace
    assert deltas[3].old_monthly == Decimal("7.592")
    assert deltas[3].new_monthly == Decimal("30.368")
    assert deltas[3].delta == Decimal("22.776")
    
    # DISK1 Create (Monthly rate as-is)
    assert deltas[4].old_monthly == Decimal("0")
    assert deltas[4].new_monthly == Decimal("19.71")
    assert deltas[4].delta == Decimal("19.71")
