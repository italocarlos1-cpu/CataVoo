# SPDX-License-Identifier: AGPL-3.0-or-later
"""DroneHighlighter — automatic editing of aerial (drone) footage.

This package is a self-contained layer on top of VideoHighlighter. It owns the
parts that are specific to drone footage — splitting long continuous takes,
telling deliberate camera moves from vibration, recognising landscape types,
and directing a montage — and hands the finished cut list to the engine's own
EDL renderer (:mod:`modules.media.edl`, :mod:`modules.media.transitions`).

Nothing in the upstream tree is modified, so upstream releases merge cleanly.

Entry points:

    python -m drone            # GUI
    python -m drone.cli ...    # command line

Copyright (C) 2026 the DroneHighlighter contributors.
Licensed under the GNU Affero General Public License v3 or later, like the
VideoHighlighter code it builds on (see LICENSE and COPYRIGHT at the repo root).
"""

__version__ = "0.5.9"

# Bump whenever the per-sample measurements change meaning: it is part of the
# cache key, so old analyses are recomputed instead of silently misread.
ANALYZER_VERSION = 1
