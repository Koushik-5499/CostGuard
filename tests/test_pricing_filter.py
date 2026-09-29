from costguard.pricing import PricingClient

def test_filter_vm_items():
    items = [
        {"meterName": "D2s v3 Low Priority", "productName": "Virtual Machines DSv3 Series Windows", "retailPrice": 0.075},
        {"meterName": "D2s v3 Spot", "productName": "Virtual Machines DSv3 Series", "retailPrice": 0.018},
        {"meterName": "D2s v3 Spot", "productName": "Virtual Machines DSv3 Series Windows", "retailPrice": 0.036},
        {"meterName": "D2s v3", "productName": "Virtual Machines DSv3 Series", "retailPrice": 0.096},
        {"meterName": "D2s v3", "productName": "Virtual Machines DSv3 Series Windows", "retailPrice": 0.188},
    ]

    # Standard Linux VM (is_windows=False, is_spot=False)
    filtered = PricingClient._filter_vm_items(items, sku="fake", is_windows=False, is_spot=False)
    assert len(filtered) == 1
    assert filtered[0]["meterName"] == "D2s v3"
    assert filtered[0]["productName"] == "Virtual Machines DSv3 Series"

    # Spot Linux VM (is_windows=False, is_spot=True)
    filtered_spot = PricingClient._filter_vm_items(items, sku="fake", is_windows=False, is_spot=True)
    assert len(filtered_spot) == 1 # Now it exclusively picks Spot!
    assert filtered_spot[0]["meterName"] == "D2s v3 Spot"

    # Standard Windows VM (is_windows=True, is_spot=False)
    filtered_win = PricingClient._filter_vm_items(items, sku="fake", is_windows=True, is_spot=False)
    assert len(filtered_win) == 1
    assert filtered_win[0]["meterName"] == "D2s v3"
    assert filtered_win[0]["productName"] == "Virtual Machines DSv3 Series Windows"

def test_pagination_and_timeout(monkeypatch):
    import urllib.request
    from costguard.pricing import PricingClient
    from costguard.cache import PricingCache
    from io import BytesIO

    cache = PricingCache(":memory:")
    client = PricingClient(cache=cache)

    call_count = 0
    def mock_urlopen(req, timeout):
        nonlocal call_count
        call_count += 1
        assert timeout == 10
        if call_count == 1:
            return BytesIO(b'{"Items": [{"meterName": "Item1"}], "NextPageLink": "http://mock"}')
        else:
            return BytesIO(b'{"Items": [{"meterName": "Item2"}]}')

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    res = client._query_api("fake_filter")
    assert len(res) == 2
    assert res[0]["meterName"] == "Item1"
    assert res[1]["meterName"] == "Item2"
    assert call_count == 2

def test_replacement_action():
    from costguard.delta import compute_action_label
    assert compute_action_label(["delete", "create"]) == "REPLACE"
    assert compute_action_label(["create", "delete"]) == "REPLACE"
