# ATLAS FAST — isolated offline development

This is a NON-DEPLOYED local Git workspace. It contains selected Python source snapshots only. No keys, live state, journals, credentials or trading permissions are copied.

NEVER execute owner_single_execution.py or any order-sending entrypoint from this workspace. No live trading.

Scope (first iteration): preserve the fail-closed 5-minute exchangeInfo freshness boundary; distinguish stale vs future timestamp errors; fix outdated risk/leverage rejection labels without changing limits.

Tests: python3 -B -m unittest discover -s tests -v
