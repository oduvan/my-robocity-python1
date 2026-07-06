"""Robot City Builder — two-mine controller (Python).

Goal of this controller: **build two mines — one ore, one metal — and have the
robots haul from both to the Base until the level-1 Base quest is done** (the
Base clears its level-1 quest by accumulating 40 ore + 20 metal, then levels up
to 2).

How it works, all driven off `idle` (the hook that fires whenever a robot is
free):

1. **Build phase.** We want one mining building on a live *ore* spot and one on a
   live *metal* spot. A mine's construction recipe is 6 ore + 3 metal — exactly
   the kit each starting robot carries. So each kit-carrying robot is assigned
   one still-missing mine: it flies to the spot, `world.build`s the site, and
   `drop`s its kit to complete it. If the spot for its mine hasn't been revealed
   yet, the robot flies into the fog to discover one first (flying reveals map).
2. **Haul phase.** Once a mine is active, free robots ferry its output to the
   Base: fly to the mine with the most stock of a resource the Base still needs,
   `pick_up`, fly home, `drop`. Repeat until the Base reaches level 2.
3. **Energy.** The Base doubles as a charging pad. Before every leg we keep a
   margin big enough to fly home; when a robot can't, it returns and `charge`s so
   it's never destroyed mid-flight.
4. **Done.** When the Base hits level 2 the level-1 quest is complete, and robots
   simply top up and stand by at the Base.
"""

import math

from simcode import buildings, on, robots, world

WANT = ("ore", "metal")        # one mine of each resource type
KIT_ORE, KIT_METAL = 6, 3      # mining construction recipe (per the game rules)
SAFE_MARGIN = 14               # energy kept in reserve so a robot can always fly home
EXPLORE_STEP = 6               # hop length when flying into the fog to find a spot
DONE_LEVEL = 2                 # Base level once the level-1 quest is cleared

# Eight compass headings used to fan out exploration when a needed spot is still
# hidden in the fog.
DIRS = [(1, 0), (0, 1), (-1, 0), (0, -1), (1, 1), (-1, 1), (-1, -1), (1, -1)]


def _euclid(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _has_kit(inv):
    return inv.ore >= KIT_ORE and inv.metal >= KIT_METAL


def _mines_by_resource():
    """resource -> its mining building (active *or* under construction).

    A mining building always reports the spot under it, so this classifies a mine
    by resource even while the site is still being built.
    """
    out = {}
    for b in buildings.of_type("mining"):
        sp = b.spot
        res = sp.resource if sp else None
        if res and res not in out:
            out[res] = b
    return out


def _taken_cells(mines):
    return {tuple(b.position) for b in mines.values() if b.position is not None}


def _nearest_spot(res, base_pos, taken):
    """Nearest discovered, non-depleted spot of `res` we haven't already mined."""
    best, best_d = None, None
    for c in world.spots():
        sp = c.spot
        if not sp or sp.resource != res:
            continue
        remaining = sp.remaining
        if remaining is not None and remaining <= 0:
            continue
        pos = c.position
        if pos is None or tuple(pos) in taken:
            continue
        d = _euclid(pos, base_pos)
        if best_d is None or d < best_d:
            best, best_d = pos, d
    return best


def _pending_resources(mines):
    """Resources whose mine is not yet ACTIVE (missing or still constructing)."""
    return [res for res in WANT
            if mines.get(res) is None or mines[res].status != "active"]


def _build_assignment(mines):
    """Map kit-carrying robot id -> the mine resource it should build.

    Only robots that actually carry a build kit can complete a mine, so we hand
    each still-missing mine to one kit-carrier (stable order → deterministic).
    """
    pend = _pending_resources(mines)
    kit_robots = sorted(r.id for r in robots.all() if _has_kit(r.inventory))
    return {rid: res for rid, res in zip(kit_robots, pend)}


def _explore(r, base_pos):
    """Fly a fresh heading outward from the Base to reveal new ground."""
    n = (r.memory.get("explore", 0) or 0) + 1
    r.memory["explore"] = n
    dx, dy = DIRS[(sum(map(ord, r.id)) + n) % len(DIRS)]
    reach = EXPLORE_STEP * (1 + n // len(DIRS))
    r.move_to(base_pos[0] + dx * reach, base_pos[1] + dy * reach)
    r.log("exploring for a resource spot")
    return True


def _try_build(r, res, mines, base_pos):
    """Advance construction of the `res` mine. Returns True if a command issued."""
    b = mines.get(res)

    # No site yet — need a live spot, then place the site and go supply it.
    if b is None:
        spot = _nearest_spot(res, base_pos, _taken_cells(mines))
        if spot is None:
            return _explore(r, base_pos)          # spot still in the fog
        world.build("mining", int(spot[0]), int(spot[1]))
        r.move_to(spot[0], spot[1])
        r.log("placing %s mine at (%d, %d)" % (res, int(spot[0]), int(spot[1])))
        return True

    if b.status == "active":
        return False                               # already built

    # Site is under construction — deliver our kit if it still needs supply.
    con = b.construction
    reqd = con.required or {}
    deld = con.delivered or {}
    need_ore = reqd.get("ore", 0) - deld.get("ore", 0)
    need_metal = reqd.get("metal", 0) - deld.get("metal", 0)
    if need_ore <= 0 and need_metal <= 0:
        return False                               # supplied; it self-completes

    tp = b.position
    if r.cell == (int(tp[0]), int(tp[1])):
        r.drop()
    else:
        r.move_to(tp[0], tp[1])
    return True


def _choose_haul_mine(mines, base):
    """Pick the active mine to haul from: a resource the Base still needs, with
    the most in stock (so we drain the fullest mine and avoid overflow)."""
    quest = base.quest
    reqd = quest.required or {}
    prog = quest.progress or {}
    need = {
        "ore": max(0, reqd.get("ore", 40) - prog.get("ore", 0)),
        "metal": max(0, reqd.get("metal", 20) - prog.get("metal", 0)),
    }
    best, best_stock = None, 0
    for res in WANT:
        if need[res] <= 0:
            continue
        b = mines.get(res)
        if b is None or b.status != "active":
            continue
        stock = b.storage.ore + b.storage.metal
        if stock > best_stock:
            best, best_stock = b, stock
    return best


@on.idle
def act(e):
    r = robots[e.robot_id]
    base = buildings.base
    if base is None:
        return
    base_pos = base.position
    base_cell = (int(base_pos[0]), int(base_pos[1]))
    at_base = r.cell == base_cell
    home_dist = _euclid(r.position, base_pos)

    # 1) Energy: always keep enough to reach the Base; recharge there when low.
    if r.energy is not None and r.energy <= home_dist + SAFE_MARGIN:
        if at_base:
            r.charge()
        else:
            r.log("low battery — returning to base to charge")
            r.move_to(base_pos[0], base_pos[1])
        return

    mines = _mines_by_resource()

    # 2) Objective reached — level-1 quest done. Deliver any cargo, then stand by.
    if base.level >= DONE_LEVEL:
        inv = r.inventory
        if (inv.ore or inv.metal) and not at_base:
            r.move_to(base_pos[0], base_pos[1])
        elif inv.ore or inv.metal:
            r.drop()
        elif at_base and r.energy is not None and r.energy < 100:
            r.charge()
        else:
            r.log("level-1 base complete — standing by")
        return

    # 3) Build phase: if this robot is assigned a mine to build, work on it.
    task = _build_assignment(mines).get(r.id)
    if task and _try_build(r, task, mines, base_pos):
        return

    # 4) Haul phase: deliver cargo to the Base, else fetch from a needed mine.
    inv = r.inventory
    if inv.ore or inv.metal:
        if at_base:
            r.drop()
        else:
            r.move_to(base_pos[0], base_pos[1])
        return

    mine = _choose_haul_mine(mines, base)
    if mine is not None:
        mp = mine.position
        if r.cell == (int(mp[0]), int(mp[1])):
            r.pick_up()
        else:
            r.move_to(mp[0], mp[1])
        return

    # 5) Nothing to haul yet (mines still filling / constructing) — wait at the
    # Base, topping up so we're ready the moment there's stock to move.
    if at_base:
        if r.energy is not None and r.energy < 100:
            r.charge()
        else:
            r.log("waiting for mines to fill")
    else:
        r.move_to(base_pos[0], base_pos[1])
