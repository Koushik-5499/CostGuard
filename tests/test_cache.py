import os
import sqlite3
import pytest
from costguard.cache import PricingCache

@pytest.fixture
def temp_cache(tmp_path):
    db_path = tmp_path / "test_cache.db"
    cache = PricingCache(str(db_path))
    yield cache
    cache.close()

def test_cache_put_get(temp_cache):
    temp_cache.put("Standard_B1s", "eastus", 0.0104)
    val = temp_cache.get("Standard_B1s", "eastus")
    assert val == 0.0104
    assert temp_cache.stats.hits == 1
    assert temp_cache.stats.misses == 0

def test_cache_miss(temp_cache):
    val = temp_cache.get("NonExistent", "eastus")
    assert val is None
    assert temp_cache.stats.hits == 0
    assert temp_cache.stats.misses == 1

def test_cache_clear(temp_cache):
    temp_cache.put("SKU1", "eastus", 1.0)
    temp_cache.clear()
    assert temp_cache.get("SKU1", "eastus") is None

def test_cache_schema(temp_cache):
    # Verify exact schema from spec
    conn = temp_cache._conn
    cursor = conn.execute("PRAGMA table_info(pricing_cache)")
    columns = {row[1]: row[2] for row in cursor.fetchall()}
    assert "sku" in columns
    assert "region" in columns
    assert "currency" in columns
    assert "hourly_rate" in columns
    assert "cached_at" in columns
    assert columns["hourly_rate"] == "REAL"
