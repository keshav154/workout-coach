"""
Periodization / mesocycle planning — structured multi-week training blocks with
a planned volume ramp and a scheduled deload, instead of week-to-week only.

A block runs `mesocycle_weeks` (a learned param): accumulation weeks where
working-set volume on primary lifts ramps up, then a final deload week. The
current week is derived from how many weeks the user has been training, so it
needs no manual bookkeeping and survives missed weeks. Deterministic — no LLM.

This coordinates with the existing per-exercise double progression: the
mesocycle says HOW MUCH volume (extra sets) and WHEN to back off; progression
still decides the weight/reps within that.
"""

import logging
from datetime import datetime

from agent_core import load_log, today

log = logging.getLogger(__name__)

MAX_EXTRA_SETS = 3          # cap the accumulation ramp so it never runs away


def _weeks_training(log: dict) -> int:
    """Whole weeks since the first logged session (0 if none)."""
    sessions = log.get("sessions", [])
    dates = []
    for s in sessions:
        try:
            dates.append(datetime.strptime(s.get("date", ""), "%Y-%m-%d").date())
        except ValueError:
            pass
    if not dates:
        return 0
    return max(0, (today() - min(dates)).days // 7)


def mesocycle_status(log: dict | None = None) -> dict:
    """Where the user is in the current block, and what it prescribes.
    Returns {week, total_weeks, phase, is_deload_week, extra_sets, focus,
    weeks_to_deload}."""
    from learned_params import get_param
    log = log or load_log()
    total = get_param("mesocycle_weeks")
    week = (_weeks_training(log) % total) + 1        # 1-based within the block
    is_deload = week == total

    if is_deload:
        phase = "deload"
        extra = 0
        focus = ("Deload week — cut volume and intensity, keep movement quality. "
                 "Next week starts a fresh block; you'll come back stronger.")
    else:
        # Accumulation: ramp extra working sets on primary lifts across the block.
        extra = min(MAX_EXTRA_SETS, week - 1)
        if week == 1:
            phase = "base"
            focus = "Base week — establish your working weights; volume is at baseline."
        else:
            phase = "accumulation"
            focus = (f"Accumulation — add {extra} working set(s) to your main lift(s) "
                     f"vs the base week to drive the volume up this block.")
    return {
        "week": week, "total_weeks": total, "phase": phase,
        "is_deload_week": is_deload, "extra_sets": extra, "focus": focus,
        "weeks_to_deload": (0 if is_deload else total - week),
    }


def format_mesocycle_block(log: dict | None = None) -> str:
    """Prompt block so the coach frames today within the training block."""
    m = mesocycle_status(log)
    line = (f"TRAINING BLOCK: week {m['week']} of {m['total_weeks']} "
            f"({m['phase']}). {m['focus']}")
    if not m["is_deload_week"] and m["weeks_to_deload"] <= 1:
        line += " (deload comes next week.)"
    return line
