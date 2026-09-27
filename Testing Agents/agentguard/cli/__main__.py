"""Enables `python -m agentguard.cli ...` (a package needs __main__.py
for that invocation style; `agentguard/cli/__init__.py`'s own
`if __name__ == "__main__"` guard only fires when that file is run
directly, not via -m on the package).
"""
from . import main

if __name__ == "__main__":
    raise SystemExit(main())
