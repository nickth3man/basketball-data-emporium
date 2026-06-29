"""DuckDB read-only connection pool."""

from basketball_data_emporium.db.pool import DuckDBPool, get_db, get_pool

__all__ = ["DuckDBPool", "get_db", "get_pool"]
