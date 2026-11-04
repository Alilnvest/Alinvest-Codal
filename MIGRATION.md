# Migration guide: 0.9.1 to 1.0.0

Version 1.0.0 keeps the established report, search, Excel, and analytics APIs while adding a typed HTTP client and a standard `src/` package layout. Existing imports such as `alilnvest_codal.income_statement`, `search_letters`, and `FinancialReport` remain available.

The new HTTP layer uses `httpx`, browser-like headers, connection pooling, bounded exponential retry with jitter, and cache lookup before a network attempt. Configure it through environment variables such as `CODAL_TIMEOUT`, `CODAL_MAX_RETRIES`, `CODAL_DELAY`, `CODAL_CACHE_TTL`, `CODAL_CACHE_DIR`, `CODAL_NO_CACHE`, and `CODAL_LOG_LEVEL`, or inject a `CodalConfig` into `CodalHTTPClient`.

```python
from alilnvest_codal import CodalConfig, CodalHTTPClient

config = CodalConfig.from_env()
with CodalHTTPClient(config) as client:
    response = client.get("https://search.codal.ir/api/search/v2/q", params={"Symbol": "فملی"})
```

The modern public wrappers are keyword-only after the symbol and accept `from_date`, `max_reports`, `use_cache`, and an injectable client. The original wrappers remain usable with their historical optional arguments, including progress callbacks and audit filters.

The 1.0 exception names are `CodalError`, `CodalNetworkError`, `CodalNotFoundError`, and `CodalParsingError`. The historical names `NetworkError`, `SymbolNotFoundError`, `DataSourceError`, and `ExcelParseError` remain exported as compatibility aliases or specialized legacy errors.

No caller action is required for pandas compatibility: the new parsing helpers avoid removed APIs such as `DataFrame.append`, `iteritems`, and `applymap`. Applications should still avoid relying on private module globals and should use `CodalConfig` or `CodalHTTPClient` for new code.
