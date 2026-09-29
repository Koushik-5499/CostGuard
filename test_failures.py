import os
import sys
from unittest.mock import patch
from costguard.cli import main

def test_network_failure():
    print("=== NETWORK FAILURE TEST ===")
    with patch("urllib.request.urlopen") as mock_urlopen:
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("Simulated Network Error")
        
        os.environ["COSTGUARD_CACHE_PATH"] = ":memory:"
        sys.argv = ["costguard", "--plan", "test-plans/plan_a_small_add.json"]
        
        try:
            main()
        except SystemExit as e:
            print(f"Exited with {e.code}")

def test_unknown_sku():
    print("\n=== UNKNOWN SKU TEST ===")
    with patch("urllib.request.urlopen") as mock_urlopen:
        from io import BytesIO
        # Return an empty Items list to simulate unknown SKU
        mock_urlopen.return_value = BytesIO(b'{"Items": []}')
        
        os.environ["COSTGUARD_CACHE_PATH"] = ":memory:"
        sys.argv = ["costguard", "--plan", "test-plans/plan_a_small_add.json"]
        
        try:
            main()
        except SystemExit as e:
            print(f"Exited with {e.code}")

if __name__ == "__main__":
    test_network_failure()
    test_unknown_sku()
