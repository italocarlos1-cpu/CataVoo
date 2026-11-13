# SPDX-License-Identifier: AGPL-3.0-or-later
"""``python -m drone`` opens the window; with arguments it runs the CLI."""
import sys

if __name__ == "__main__":
    if len(sys.argv) > 1:
        from drone.cli import main
    else:
        from drone.gui import main
    raise SystemExit(main())
