"""Double-click / `python run_tracker.py` shim for `python -m roblox_tracker`."""
import sys

from roblox_tracker.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
