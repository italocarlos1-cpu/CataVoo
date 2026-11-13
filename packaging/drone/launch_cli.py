# SPDX-License-Identifier: AGPL-3.0-or-later
"""Entry point of DroneHighlighter-cli.exe (command line, with a console)."""
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from drone.cli import main
    raise SystemExit(main())
