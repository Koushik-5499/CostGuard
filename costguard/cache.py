"""SQLite write-through cache for Azure pricing data."""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass, field
from decimal import Decimal


# Default cache database path is now evaluated at runtime
CACHE_TTL_SECONDS = int(os.environ.get("COSTGUARD_CACHE_TTL", 604800))

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS pricing_cache (
    sku TEXT NOT NULL,
    region TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    hourly_rate REAL NOT NULL,
    cached_at INTEGER NOT NULL,
    PRIMARY KEY (sku, region, currency)
);
"""


@dataclass
class CacheStats:
    """Track cache hit/miss statistics."""
    hits: int = 0
    misses: int = 0
    api_lookups: int = 0


class PricingCache:
    """SQLite write-through cache for pricing data.

    Schema matches the spec exactly:
        sku TEXT, region TEXT, currency TEXT, hourly_rate REAL, cached_at INTEGER
        PRIMARY KEY (sku, region, currency)

    Note: hourly_rate stores the per-hour rate for VMs, and the monthly rate
    for disks (where unitOfMeasure is '1/Month'). The 'unit' distinction is
    handled by the caller via the is_monthly flag in PriceResult.
    """

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_path = os.environ.get(
                "COSTGUARD_CACHE_PATH", 
                os.path.expanduser("~/.costguard/pricing_cache.db")
            )
        self.db_path = db_path
        self.stats = CacheStats()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self):
        """Initialize the database and create the table if needed."""
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute(CREATE_TABLE_SQL)
        self._conn.commit()

    def get(self, sku: str, region: str, currency: str = "USD") -> float | None:
        """Look up a cached price. Returns the rate or None on miss."""
        cursor = self._conn.execute(
            "SELECT hourly_rate, cached_at FROM pricing_cache WHERE sku = ? AND region = ? AND currency = ?",
            (sku, region, currency),
        )
        row = cursor.fetchone()
        if row is not None:
            rate, cached_at = row
            now = int(time.time())
            if now - cached_at <= CACHE_TTL_SECONDS:
                self.stats.hits += 1
                return rate
        self.stats.misses += 1
        return None

    def put(self, sku: str, region: str, rate: float, currency: str = "USD"):
        """Store a price in the cache (write-through)."""
        now = int(time.time())
        self._conn.execute(
            """INSERT OR REPLACE INTO pricing_cache (sku, region, currency, hourly_rate, cached_at)
               VALUES (?, ?, ?, ?, ?)""",
            (sku, region, currency, rate, now),
        )
        self._conn.commit()

    def clear(self):
        """Purge all cached entries."""
        self._conn.execute("DELETE FROM pricing_cache")
        self._conn.commit()

    def close(self):
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __del__(self):
        self.close()
