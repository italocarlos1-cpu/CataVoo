# SPDX-License-Identifier: AGPL-3.0-or-later
"""Entry point of DroneHighlighter.exe (the window)."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) > 1:                 # arguments → behave like the CLI
        from drone.cli import main
    else:
        from drone.gui import main
    raise SystemExit(main())
