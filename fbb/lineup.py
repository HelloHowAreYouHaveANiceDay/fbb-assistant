"""Daily lineup optimisation.

Given: our roster for one scoring period (day), each man's eligible slots, whether he is locked, and a
projected value for that day (0 when he has no game or is ruled out), choose the legal assignment of
men to STARTING slots that maximises the projected total, then emit the fewest LINEUP moves that turn
ESPN's current lineup into it.

Rules:
- A LOCKED man (his game has started / ESPN says lineupLocked) does not move, and his slot stays his.
- IR men are left alone (moving someone off IR is a roster decision, not a lineup one).
- Ties prefer the man already in the slot, so the job never churns the lineup for nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from .consts import BENCH, IR, SLOTS


@dataclass
class Man:
    pid: int
    name: str
    slot: int                      # current lineupSlotId
    eligible: list[int]
    locked: bool
    value: float                   # projected fantasy points today (0 if no game / out)
    has_game: bool = True
    note: str = ""
    extra: dict = field(default_factory=dict)


def starting_slots(slot_counts: dict) -> list[int]:
    out = []
    for sid, n in sorted(((int(k), int(v)) for k, v in slot_counts.items())):
        if sid in (BENCH, IR):
            continue
        out += [sid] * n
    return out


def optimise(men: list[Man], slot_counts: dict) -> dict[int, int]:
    """Return {pid: target slot} for every non-IR man (BENCH for non-starters)."""
    slots = starting_slots(slot_counts)
    movable = [m for m in men if m.slot != IR and not m.locked]
    locked = [m for m in men if m.slot != IR and m.locked]
    # locked starters keep their slot -> remove one copy of each from the pool
    free = list(slots)
    for m in locked:
        if m.slot in free:
            free.remove(m.slot)
    free_t = tuple(sorted(free))
    n = len(movable)

    @lru_cache(maxsize=None)
    def best(i: int, remaining: tuple) -> tuple[float, tuple]:
        if i == n:
            return 0.0, ()
        m = movable[i]
        # option: bench him (tiny bonus if he is already benched = stability)
        b_val, b_rest = best(i + 1, remaining)
        top = (b_val + (1e-6 if m.slot == BENCH else 0.0), (BENCH,) + b_rest)
        if m.value > 0 or m.slot != BENCH:   # a zero-value starter may KEEP his slot (no churn)
            seen = set()
            for j, s in enumerate(remaining):
                if s in seen or s not in m.eligible:
                    continue
                seen.add(s)
                rest = remaining[:j] + remaining[j + 1:]
                v, r = best(i + 1, rest)
                v += m.value + (1e-6 if m.slot == s else 0.0)
                if v > top[0] + 1e-9:
                    top = (v, (s,) + r)
        return top

    _, assign = best(0, free_t)
    target = {m.pid: m.slot for m in locked}
    target.update({m.pid: s for m, s in zip(movable, assign)})
    return target


def moves_for(men: list[Man], target: dict[int, int]) -> list[dict]:
    """LINEUP items for every man whose slot changes. ESPN applies the set atomically."""
    return [{"playerId": m.pid, "fromLineupSlotId": m.slot, "toLineupSlotId": target[m.pid]}
            for m in men if m.pid in target and target[m.pid] != m.slot]


def total(men: list[Man], assign: dict[int, int]) -> float:
    return sum(m.value for m in men if assign.get(m.pid, m.slot) not in (BENCH, IR))


def describe(men: list[Man], target: dict[int, int]) -> list[str]:
    by = {m.pid: m for m in men}
    lines = []
    for pid, s in sorted(target.items(), key=lambda kv: (kv[1] in (BENCH, IR), kv[1])):
        m = by[pid]
        lines.append(f"{SLOTS.get(s, s):>6}  {m.name} ({m.value:.1f}){'  [locked]' if m.locked else ''}{'  ' + m.note if m.note else ''}")
    return lines
