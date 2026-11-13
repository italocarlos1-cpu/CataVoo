# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pick N shots that are individually good *and* different from each other.

Greedy Maximal Marginal Relevance: each pick maximises

    (1 − w_variety) · base_score  +  w_variety · (1 − max similarity to picks)

with hard limits: nothing above ``duplicate_threshold`` similarity to an
already chosen shot, at most ``max_shots_per_clip`` shots from one file, and a
soft penalty once one landscape category holds more than its share.
"""
from __future__ import annotations

import math
from collections import Counter

from drone.duplicate_detection import max_similarity


def select(candidates: list, n: int, *, w_variety: float, duplicate_threshold: float,
           max_per_clip: int, extra_bonus=None, already=(), relax: bool = True,
           log_fn=print) -> list:
    """``already``: shots picked earlier, which new picks must differ from."""
    pool = [c for c in candidates if c not in already]
    chosen: list = []
    context = list(already)
    per_clip: Counter = Counter(c.clip for c in already)
    per_cat: Counter = Counter(c.category for c in already)
    share = max(2, math.ceil(n / 3))
    threshold = duplicate_threshold
    while len(chosen) < n and pool:
        best, best_val = None, -1e9
        for c in pool:
            if per_clip[c.clip] >= max_per_clip:
                continue
            sim = max_similarity(c, context + chosen)
            if sim > threshold:
                continue
            val = (1.0 - w_variety) * c.base + w_variety * (1.0 - sim)
            if per_cat[c.category] >= share:
                val *= 0.85
            if extra_bonus is not None:
                val += extra_bonus(c)
            if val > best_val:
                best, best_val = c, val
        if best is None:
            # Ran out of distinct material: relax the limits rather than
            # deliver a reel shorter than asked; repeats are still last resort.
            if relax and threshold < 0.995:
                threshold = min(0.995, threshold + 0.03)
                max_per_clip += 1
                log_fn("ℹ️ Pouco material distinto: relaxando o limite de semelhança")
                continue
            break
        chosen.append(best)
        per_clip[best.clip] += 1
        per_cat[best.category] += 1
        pool.remove(best)
    return chosen
