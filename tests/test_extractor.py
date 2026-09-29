import pytest
from costguard.extractor import extract_changes, normalize_region

def test_normalize_region():
    assert normalize_region("East US") == "eastus"
    assert normalize_region(" west US 2 ") == "westus2"
    assert normalize_region(None) is None

def test_extract_changes_vm():
    plan = {
        "resource_changes": [
            {
                "address": "azurerm_linux_virtual_machine.test",
                "type": "azurerm_linux_virtual_machine",
                "change": {
                    "actions": ["create"],
                    "after": {
                        "location": "East US",
                        "size": "Standard_B1s",
                        "priority": "Spot"
                    }
                }
            },
            {
                "address": "azurerm_windows_virtual_machine.win",
                "type": "azurerm_windows_virtual_machine",
                "change": {
                    "actions": ["update"],
                    "before": {
                        "location": "westus",
                        "size": "Standard_D2s_v3"
                    },
                    "after": {
                        "location": "westus",
                        "size": "Standard_D4s_v3"
                    }
                }
            }
        ]
    }
    
    changes = extract_changes(plan)
    assert len(changes) == 2
    
    c1 = changes[0]
    assert c1.address == "azurerm_linux_virtual_machine.test"
    assert c1.actions == ["create"]
    assert c1.region_after == "eastus"
    assert c1.sku_after == "Standard_B1s"
    assert c1.is_spot is True
    assert c1.is_windows is False
    
    c2 = changes[1]
    assert c2.address == "azurerm_windows_virtual_machine.win"
    assert c2.actions == ["update"]
    assert c2.sku_before == "Standard_D2s_v3"
    assert c2.sku_after == "Standard_D4s_v3"
    assert c2.is_windows is True
    assert c2.is_spot is False

def test_extract_changes_disk():
    plan = {
        "resource_changes": [
            {
                "address": "azurerm_managed_disk.data",
                "type": "azurerm_managed_disk",
                "change": {
                    "actions": ["create"],
                    "after": {
                        "location": "eastus",
                        "storage_account_type": "Premium_LRS",
                        "disk_size_gb": 128
                    }
                }
            }
        ]
    }
    changes = extract_changes(plan)
    assert len(changes) == 1
    assert changes[0].disk_storage_type_after == "Premium_LRS"
    assert changes[0].disk_size_gb_after == 128

def test_extract_changes_skip_non_billable():
    plan = {
        "resource_changes": [
            {
                "address": "azurerm_resource_group.rg",
                "type": "azurerm_resource_group",
                "change": {
                    "actions": ["create"],
                    "after": {"location": "eastus"}
                }
            }
        ]
    }
    assert len(extract_changes(plan)) == 0

def test_extract_changes_unknown_size_skipped(capsys):
    plan = {
        "resource_changes": [
            {
                "address": "azurerm_linux_virtual_machine.unknown",
                "type": "azurerm_linux_virtual_machine",
                "change": {
                    "actions": ["create"],
                    "after_unknown": {"size": True},
                    "after": {"location": "eastus"}
                }
            }
        ]
    }
    changes = extract_changes(plan)
    assert len(changes) == 0
    captured = capsys.readouterr()
    assert "VM size unknown at plan time" in captured.err
