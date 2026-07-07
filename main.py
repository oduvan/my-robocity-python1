"""SimCode city controller — resource-managed economy.

Goal: climb Base levels as high as possible without ever starving the city of
building materials. The whole design is built around ONE discipline the user
called out explicitly: **never spend so much on robots/expansion that we can no
longer afford a mine.** Every expansion is gated behind a hard storage RESERVE.

Flow the fleet follows (all decided on `@on.idle`):

    bootstrap 1 ore mine + 1 metal mine  ->  haul their output to the Base to
    fill the quest (surplus goes to Storage)  ->  Base levels up  ->  build MORE
    mines as the quest grows (only while Storage stays above reserve)  ->  once
    truly flush, optionally add a Flying Station + a few robots to haul more.

Mining rate is the real bottleneck (1/tick per mine), so the primary lever is
"more mines", not "more robots". Robots are the last thing we spend on.
"""

import math

from simcode import on, robots, buildings, world, store

# ---- world constants (from the module config, seed 7) ----------------------
CARRY = 10
ENERGY_CAP = 100
MINE_ORE, MINE_METAL = 6, 3          # mining recipe
STA_ORE, STA_METAL = 4, 2            # flying_station recipe
ROBOT_ORE, ROBOT_METAL = 12, 6       # a station spends this per robot it builds

# The safety net. Haulers keep at least the FLOOR of each resource in Storage
# *before* feeding the quest, so we can ALWAYS afford to (re)build a mine — the
# critical case being metal: a metal mine itself costs 3 metal, so if the quest
# ate every scrap of metal we could never replace a depleted metal mine.
FLOOR_ORE, FLOOR_METAL = 20, 10
# Discretionary spends (stations/robots) may only draw Storage down to RESERVE,
# never through it — that keeps a mine affordable after any expansion.
RESERVE_ORE, RESERVE_METAL = 12, 6

CHARGE_MARGIN = 25      # spare battery to keep on top of the trip to a pad
FLEET_CAP = 5           # never grow the fleet past this
MIN_SPOT_REMAINING = 15 # ignore nearly-exhausted spots when siting a mine
MAX_MINE_DIST = 24      # only site mines this close to a charging pad, so every
                        # haul round-trip stays well inside one battery (keeps
                        # robots from ever stranding out in the fog)

BASE = (0, 0)
DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]


# ---- tiny helpers ----------------------------------------------------------
def _init():
    store.setdefault("planned", [])   # active build jobs (mines / stations)
    store.setdefault("dead", [])      # ids of mines whose spot is depleted
    store.setdefault("sites", [])     # coords where a site was placed


def _g(o, k, d=0):
    """Read a field whether the SDK hands back an object or a dict."""
    try:
        if isinstance(o, dict):
            return o.get(k, d)
        return getattr(o, k, d)
    except Exception:
        return d


def rc(p):
    return (int(round(p[0])), int(round(p[1])))


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def inv(r):
    i = r.inventory
    return _g(i, "ore"), _g(i, "metal")


def _all_robots():
    try:
        return list(robots.all())
    except Exception:
        return []


# ---- reading the world -----------------------------------------------------
def quest_need():
    """How much ore/metal the current quest still needs."""
    q = buildings.base.quest
    req, prog = _g(q, "required"), _g(q, "progress")
    return (max(0, _g(req, "ore") - _g(prog, "ore")),
            max(0, _g(req, "metal") - _g(prog, "metal")))


def base_level():
    return _g(buildings.base, "level", 1) or 1


def storages():
    return [b for b in buildings.of_type("storage")
            if _g(b, "status", "active") == "active"]


def primary_storage():
    ss = storages()
    return min(ss, key=lambda b: dist(rc(b.position), BASE)) if ss else None


def storage_totals():
    o = m = 0
    for b in storages():
        st = b.storage
        o += _g(st, "ore"); m += _g(st, "metal")
    return o, m


def mines_all():
    return list(buildings.of_type("mining"))


def mine_res(b):
    sp = _g(b, "spot", None)
    return _g(sp, "resource", None) if sp else None


def productive(b):
    """A mine counts toward our targets only while it can still dig — active and
    with a non-empty spot. This is self-correcting: a spot that hits 0 stops
    counting immediately, so a replacement gets built even if we never saw the
    `spot_depleted` event."""
    if b.id in set(store["dead"]):
        return False
    if _g(b, "status", "") != "active":
        return False
    sp = _g(b, "spot", None)
    if sp is not None and _g(sp, "remaining", 1) <= 0:
        return False
    return True


def live_mines():
    return [b for b in mines_all() if productive(b)]


def haulable_mines():
    """Active mines with output sitting in their store (depleted ones count —
    they may still hold un-hauled resources)."""
    out = []
    for b in mines_all():
        if _g(b, "status", "") != "active":
            continue
        st = b.storage
        if _g(st, "ore") + _g(st, "metal") > 0:
            out.append(b)
    return out


def count_have(res):
    """Productive mines of a resource we already have or have committed to
    (still-digging, constructing, or planned) — so we don't over-build, but a
    depleted mine no longer counts so it gets replaced."""
    n = 0
    seen = set()
    for b in mines_all():
        c = rc(b.position)
        # count a real mine if it's productive, OR still under construction
        constructing = _g(b, "status", "") != "active"
        if (productive(b) or constructing) and mine_res(b) == res:
            seen.add(c)
            n += 1
    for j in store["planned"]:
        if j["btype"] == "mining" and j["res"] == res and tuple(j["xy"]) not in seen:
            n += 1
    return n


def occupied():
    occ = set()
    for b in mines_all():
        occ.add(rc(b.position))
    for c in store["sites"]:
        occ.add(tuple(c))
    return occ


def pick_spot(res, occ):
    """Nearest un-taken, non-trivial spot of a resource that sits within a safe
    haul radius of a charging pad."""
    ps = pads()
    best, bd = None, 1e18
    for s in world.spots():
        sp = _g(s, "spot", None)
        if not sp or _g(sp, "resource") != res:
            continue
        if _g(sp, "remaining", 0) < MIN_SPOT_REMAINING:
            continue
        c = rc(s.position)
        if c in occ:
            continue
        if min(dist(c, p) for p in ps) > MAX_MINE_DIST:
            continue                     # too far from any pad — would risk stranding
        d = dist(c, BASE)
        if d < bd:
            bd, best = d, c
    return best


# ---- charging (stay alive) -------------------------------------------------
def pads():
    ps = [BASE]
    for b in buildings.of_type("flying_station"):
        if _g(b, "status", "") == "active":
            ps.append(rc(b.position))
    return ps


def nearest_pad(r):
    return min(pads(), key=lambda c: dist(r.position, c))


def need_charge(r):
    if r.energy is None:
        return False
    p = nearest_pad(r)
    return r.energy < dist(r.position, p) + CHARGE_MARGIN


def go_charge(r):
    p = nearest_pad(r)
    if r.cell == p:
        r.charge()
    else:
        r.move_to(*p)


def fly(r, tx, ty):
    """Move — but never commit to a flight the robot can't come back from. If
    reaching (tx,ty) and then the nearest pad from there would cost more than we
    can spare, charge first. This is the hard guarantee against mid-flight death."""
    if r.energy is None:
        r.move_to(tx, ty)
        return
    tgt = (tx, ty)
    pad_after = min(pads(), key=lambda c: dist(tgt, c))
    cost = dist(r.position, tgt) + dist(tgt, pad_after)
    if r.energy >= cost + CHARGE_MARGIN:
        r.move_to(tx, ty)
    else:
        go_charge(r)


# ---- expansion policy (all reserve-gated) ----------------------------------
def targets():
    """Desired mine count per resource. Scales with Base level, but is capped by
    how many mines our current fleet can actually keep hauled — building mines we
    can't empty is wasted materials. Weighted ~2:1 ore:metal (the quest need)."""
    lvl = base_level()
    fleet = max(1, len(_all_robots()))
    cap = fleet * 2 + 1                  # a robot comfortably services ~2 short mines
    total = max(2, min(8, 2 + lvl // 2, cap))
    tm = max(1, total // 3)
    to = total - tm
    return to, tm


def can_afford_mine():
    # Mines are the priority spend and the whole point of the reserve — so a mine
    # is buildable whenever the recipe is on hand. The RESERVE only guards the
    # discretionary spends (stations / robots) below, never a mine.
    so, sm = storage_totals()
    return so >= MINE_ORE and sm >= MINE_METAL


def has_station():
    if any(True for _ in buildings.of_type("flying_station")):
        return True
    return any(j["btype"] == "flying_station" for j in store["planned"])


def stations_active():
    return [b for b in buildings.of_type("flying_station")
            if _g(b, "status", "") == "active"]


def want_station():
    if has_station() or base_level() < 2 or len(live_mines()) < 2:
        return False
    so, sm = storage_totals()            # only from surplus that stays above reserve
    return so >= STA_ORE + RESERVE_ORE and sm >= STA_METAL + RESERVE_METAL


def desired_robots():
    # Grow the fleet with Base level (more haulers = the main throughput lever).
    # Tied to level, not live-mine count, so it can't deadlock against the mine
    # target — which is itself capped by fleet size.
    return min(FLEET_CAP, 2 + base_level() // 2)


def fund_task_active():
    for r in _all_robots():
        t = r.memory.get("task")
        if t and t.get("kind") == "fund":
            return True
    return False


def want_robot():
    if not stations_active() or fund_task_active():
        return False
    if len(_all_robots()) >= desired_robots():
        return False
    # More haulers is the main throughput lever, and we only get here with a
    # station and plenty of mines — so fund a robot whenever we can still afford
    # a mine (6/3) afterwards. That keeps the anti-starvation guarantee intact.
    so, sm = storage_totals()
    return so - ROBOT_ORE >= MINE_ORE and sm - ROBOT_METAL >= MINE_METAL


def free_build_cell(occ):
    """A buildable ground cell near the Base for a Flying Station: not a spot,
    not on any building, not the Base."""
    bad = set(occ) | {BASE}
    for s in world.spots():
        bad.add(rc(s.position))
    for b in buildings.all():
        p = rc(b.position)
        w = int(_g(b, "footprint", (1, 1))[0]) if _g(b, "footprint", None) else 1
        h = int(_g(b, "footprint", (1, 1))[1]) if _g(b, "footprint", None) else 1
        for dx in range(w):
            for dy in range(h):
                bad.add((p[0] + dx, p[1] + dy))
    for rad in range(2, 15):
        for dx in range(-rad, rad + 1):
            for dy in range(-rad, rad + 1):
                if max(abs(dx), abs(dy)) != rad:
                    continue
                c = (dx, dy)
                if c not in bad:
                    return c
    return None


# ---- build/fund task execution ---------------------------------------------
def prune_planned():
    """Drop jobs whose target building is now active (completed)."""
    live_coords = set()
    for b in mines_all():
        if _g(b, "status", "") == "active":
            live_coords.add(rc(b.position))
    for b in buildings.of_type("flying_station"):
        if _g(b, "status", "") == "active":
            live_coords.add(rc(b.position))
    store["planned"] = [j for j in store["planned"]
                        if tuple(j["xy"]) not in live_coords]


def run_build(r):
    xy = tuple(r.memory["task"]["xy"])
    job = next((j for j in store["planned"] if tuple(j["xy"]) == xy), None)
    if job is None:                      # already completed by someone/prune
        r.memory["task"] = None
        return decide_next(r)

    need_o, need_m, btype = job["ore"], job["metal"], job["btype"]
    io, im = inv(r)

    # 1) gather the recipe from Storage
    if io < need_o or im < need_m:
        ps = primary_storage()
        if ps is None:
            job["builder"] = None
            r.memory["task"] = None
            return decide_next(r)
        sc = rc(ps.position)
        if r.cell == sc:
            so, sm = storage_totals()
            if so < need_o or sm < need_m:   # not enough right now: release, go haul
                job["builder"] = None
                r.memory["task"] = None
                return decide_next(r)
            r.pick_up(ore=need_o - io, metal=need_m - im)
        else:
            fly(r, *sc)
        return

    # 2) place the site once, then deliver the recipe onto it
    if not job.get("site"):
        world.build(btype, xy[0], xy[1])
        job["site"] = True
        if list(xy) not in store["sites"]:
            store["sites"].append(list(xy))
    if r.cell == xy:
        r.drop(ore=need_o, metal=need_m)   # site self-completes
        job["builder"] = None
        job["delivered"] = True            # keep in planned until it goes active
    else:
        fly(r, *xy)


def run_fund(r):
    """Stock a Flying Station up to one robot's cost (12 ore + 6 metal), then
    build it. A robot only carries 10, so this is MULTI-TRIP: haul into the
    station's own store across trips until it holds enough. Never draws Storage
    below the mine-building reserve."""
    t = r.memory["task"]
    sid, xy = t["sid"], tuple(t["xy"])
    st = next((b for b in buildings.of_type("flying_station") if b.id == sid), None)
    if st is None or _g(st, "status", "") != "active":
        r.memory["task"] = None
        return decide_next(r)

    sto = st.storage
    need_o = max(0, ROBOT_ORE - _g(sto, "ore"))
    need_m = max(0, ROBOT_METAL - _g(sto, "metal"))

    # station is fully stocked -> build the robot and finish
    if need_o <= 0 and need_m <= 0:
        try:
            st.build_robot(1)
        except Exception:
            pass
        r.memory["task"] = None
        return decide_next(r)

    io, im = inv(r)

    # carrying materials -> deliver them into the station store
    if io > 0 or im > 0:
        if r.cell == xy:
            r.drop(ore=min(io, need_o), metal=min(im, need_m))
        else:
            fly(r, *xy)
        return

    # empty -> load what the station still needs (metal first: it's the scarce one),
    # keeping Storage above the mine-building reserve
    ps = primary_storage()
    if ps is None:
        r.memory["task"] = None
        return decide_next(r)
    sc = rc(ps.position)
    if r.cell == sc:
        so, sm = storage_totals()
        take_m = min(need_m, max(0, sm - MINE_METAL), CARRY)
        take_o = min(need_o, max(0, so - MINE_ORE), CARRY - take_m)
        if take_o <= 0 and take_m <= 0:   # can't help without breaking reserve
            r.memory["task"] = None
            return decide_next(r)
        r.pick_up(ore=take_o, metal=take_m)
    else:
        fly(r, *sc)


def _new_job(r, xy, res, btype, o, m):
    store["planned"].append({"xy": list(xy), "res": res, "btype": btype,
                             "ore": o, "metal": m, "builder": r.id,
                             "site": False, "delivered": False})
    r.memory["task"] = {"kind": "build", "xy": list(xy)}
    run_build(r)
    return True


def assign_build(r):
    """If the city wants something built and can afford it, hand this idle empty
    robot the job. Returns True if it took one."""
    prune_planned()
    live_ids = {rr.id for rr in _all_robots()}

    # 0) resume an orphaned job (its builder died / released it)
    for j in store["planned"]:
        if j.get("delivered"):
            continue
        if j.get("builder") not in live_ids:
            j["builder"] = r.id
            r.memory["task"] = {"kind": "build", "xy": list(j["xy"])}
            run_build(r)
            return True

    to, tm = targets()
    occ = occupied()
    have_o, have_m = count_have("ore"), count_have("metal")
    live = len(live_mines())
    fleet = len(_all_robots())

    def build_mine():
        """Build whichever resource is furthest below its target."""
        if not can_afford_mine():
            return False
        gap_o, gap_m = to - have_o, tm - have_m
        order = ("ore", "metal") if gap_o >= gap_m else ("metal", "ore")
        for res in order:
            tgt = to if res == "ore" else tm
            if count_have(res) < tgt:
                c = pick_spot(res, occ)
                if c:
                    _new_job(r, c, res, "mining", MINE_ORE, MINE_METAL)
                    return True
        return False

    def grow_fleet():
        """A Flying Station, then robots — the throughput lever."""
        if want_station():
            c = free_build_cell(occ)
            if c:
                _new_job(r, c, None, "flying_station", STA_ORE, STA_METAL)
                return True
        if want_robot():
            st = stations_active()[0]
            r.memory["task"] = {"kind": "fund", "sid": st.id,
                                "xy": list(rc(st.position))}
            run_fund(r)
            return True
        return False

    # 1) essential: never be left without a source of either resource
    if have_o == 0 and build_mine():
        return True
    if have_m == 0 and build_mine():
        return True

    # 2) balance mines against haul capacity. While the fleet has spare capacity
    # (fewer mines than it can service) add mines; once mines saturate the
    # haulers, spend on growing the fleet instead. This is what stops depletion
    # churn from perpetually preempting fleet growth.
    if live < fleet * 2:
        if build_mine():
            return True
        if grow_fleet():
            return True
    else:
        if grow_fleet():
            return True
        if build_mine():
            return True

    return False


# ---- hauling & delivery ----------------------------------------------------
def deliver(r):
    """Carry cargo somewhere useful. Order matters: top up the Storage build
    reserve FIRST (so a replacement mine is always affordable — metal especially),
    then feed the quest, then dump any remaining surplus back into Storage."""
    io, im = inv(r)
    ps = primary_storage()
    sc = rc(ps.position) if ps else None

    # 1) keep the building-materials floor topped up in Storage
    if ps is not None:
        so, sm = storage_totals()
        put_o = min(io, max(0, FLOOR_ORE - so))
        put_m = min(im, max(0, FLOOR_METAL - sm))
        if put_o > 0 or put_m > 0:
            if r.cell == sc:
                r.drop(ore=put_o, metal=put_m)
            else:
                fly(r, *sc)
            return

    # 2) feed the quest
    no, nm = quest_need()
    if (io > 0 and no > 0) or (im > 0 and nm > 0):
        if r.cell == BASE:
            r.drop(ore=io, metal=im)     # Base caps at the requirement, returns excess
        else:
            fly(r, *BASE)
        return

    # 3) leftover surplus beyond the quest -> Storage buffer (funds expansion)
    if ps is not None:
        if r.cell == sc:
            r.drop(ore=io, metal=im)
        else:
            fly(r, *sc)
        return

    # no Storage at all: sit on the Base pad rather than wander
    if r.cell == BASE:
        r.charge()
    else:
        r.move_to(*BASE)


def decide_haul(r):
    """Pick the best mine to empty: prefer the resource the quest still needs,
    then the fullest, then the nearest."""
    no, nm = quest_need()
    best, bscore = None, None
    for b in haulable_mines():
        st = b.storage
        out = _g(st, "ore") + _g(st, "metal")
        res = mine_res(b)
        needed = (res == "ore" and no > 0) or (res == "metal" and nm > 0)
        c = rc(b.position)
        score = (1 if needed else 0, out, -dist(r.position, c))
        if bscore is None or score > bscore:
            bscore, best = score, b
    if best is not None:
        c = rc(best.position)
        if r.cell == c:
            r.pick_up()                  # take everything that fits
        else:
            fly(r, *c)
        return
    # nothing to haul yet: park on a pad topped-up, ready to move the instant
    # a mine produces; explore a little once full to reveal spots for expansion
    if r.energy is not None and r.energy < ENERGY_CAP - 5:
        go_charge(r)
    else:
        _explore(r)


def _explore(r):
    n = r.memory.get("hop", 0) + 1
    r.memory["hop"] = n
    x, y = r.position
    if dist((x, y), BASE) > 15:          # never drift far from the pad
        fly(r, *BASE)
        return
    dx, dy = DIRS[n % len(DIRS)]
    fly(r, x + dx * 4, y + dy * 4)


def decide_next(r):
    io, im = inv(r)
    if io > 0 or im > 0:                  # carrying something -> deliver it
        return deliver(r)
    if assign_build(r):                  # empty -> maybe build/expand
        return
    return decide_haul(r)                # otherwise haul mine output


# ---- events ----------------------------------------------------------------
@on.idle
def act(e):
    _init()
    r = robots[e.robot_id]
    if r is None:
        return
    if need_charge(r):                   # safety always wins
        go_charge(r)
        return
    t = r.memory.get("task")
    if t and t.get("kind") == "build":
        return run_build(r)
    if t and t.get("kind") == "fund":
        return run_fund(r)
    decide_next(r)


@on.spot_depleted
def on_depleted(e):
    _init()
    bid = _g(e, "building_id", None)
    if bid and bid not in store["dead"]:
        store["dead"].append(bid)        # stop counting it; a replacement gets built


@on.construction_complete
def on_complete(e):
    _init()
    prune_planned()


@on.robot_destroyed
def on_destroyed(e):
    _init()
    rid = _g(e, "robot_id", None)
    for j in store["planned"]:           # free any job the lost robot was building
        if j.get("builder") == rid:
            j["builder"] = None
