"""Compatibility launcher; use python -m volatility_lab (add --lan for LAN access)."""
from volatility_lab.runtime.launcher import main

if __name__ == "__main__":
    raise SystemExit(main())
