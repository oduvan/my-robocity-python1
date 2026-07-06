"""Robot City Builder — starter controller (Python).

Robots start EMPTY. The ONLY way to shape the city is with the code below: it
reacts to `idle` events (a robot is free) and hands each robot its next command.

The economy in one breath:
  * A pre-placed **Storage** next to the Base holds your starting capital
    (30 ore / 15 metal). Robots `pick_up` from it to get build materials.
  * **Mines** you build (place a site on a resource spot with
    `world.build("mining", x, y)`, then `drop` its 6-ore / 3-metal cost onto the
    site) auto-produce ore or metal. Robots `pick_up` from a mine and haul it off.
  * The **Base** is the quest hub: `drop` ore/metal on it to fill the current
    quest; meet it and the Base LEVELS UP to a harder one. Highest level = score.
    You can't pick up from the Base; it doubles as a charging pad.
  * A **Flying Station** (`world.build("flying_station", x, y)`) is a charging pad
    AND a robot factory: stock it (drop ore/metal), then `station.build_robot(1)`.
  * Flying burns ENERGY; `charge()` on any pad (Base or Flying Station). A robot
    that runs dry mid-flight is destroyed — so `approach()` below never starts a
    trip the robot can't finish and still reach a pad.

Priority per idle robot:
  1. energy guard    — get to a pad and charge before the battery dies,
  2. place mines     — ensure an ore mine AND a metal mine exist,
  3. fund sites      — carry materials to any half-built site (mine / station),
  4. grow (once)     — after level 1, one robot builds a Flying Station + 1 robot,
  5. haul the quest  — carry mine output to the Base to climb levels,
  6. explore         — reveal the fog when there's nothing better to do.

Persistent state lives in the durable, city-wide `store` and per-robot `memory`
(never module globals — a code push hot-reloads this file and resets module scope).
"""

from simcode import buildings, on, robots, store, world

MINE_KIT = (6, 3)       # ore, metal — the Mining recipe (what one mine costs)
STATION_KIT = (4, 2)    # the Flying Station recipe
ROBOT_COST = (12, 6)    # what a station spends from its store to build one robot
CARRY = 10              # robot inventory capacity (ore + metal together)
ENERGY_MARGIN = 20      # battery kept spare on top of any planned round trip

# Compass headings for exploration; a robot advances one per trip so the fleet
# fans out across the map instead of re-treading a single line.
DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]


# --------------------------------------------------------------------------- #
# small read-only helpers over the live world
# --------------------------------------------------------------------------- #
def dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def charging_pads():
    """Every cell a robot can `charge()` on: the Base + each active station."""
    pads = [buildings.base.position] if buildings.base else []
    return pads + [s.position for s in buildings.stations() if s.status == "active"]


def occupied_cells():
    """Every cell covered by a building, so we never stack a new site on one."""
    cells = set()
    for b in buildings.all():
        if not b.position:
            continue
        w, h = b.footprint
        for dx in range(w):
            for dy in range(h):
                cells.add((b.position[0] + dx, b.position[1] + dy))
    return cells


def mine_resources():
    """Resources we have a PRODUCTIVE mine for (built or under construction, spot
    not yet exhausted). A mine whose spot has run dry no longer counts, so the
    controller builds a fresh mine to replace it — the climb never hard-stalls."""
    return {m.spot.resource for m in buildings.of_type("mining")
            if m.spot and m.spot.resource and (m.spot.remaining or 0) > 0}


def free_spot(pos, resource):
    """Nearest discovered, still-rich spot of `resource` with no building on it."""
    occ = occupied_cells()
    best = None
    for c in world.spots():
        if not c.spot or c.spot.resource != resource or (c.spot.remaining or 0) <= 0:
            continue
        if (c.x, c.y) in occ:
            continue
        if best is None or dist(pos, (c.x, c.y)) < dist(pos, best):
            best = (c.x, c.y)
    return best


def nearest_building(pos, kind, want=None):
    """Nearest ACTIVE building of `kind`; `want(b)` may filter (e.g. has stock)."""
    best = None
    for b in buildings.of_type(kind):
        if b.status != "active" or (want and not want(b)):
            continue
        if best is None or dist(pos, b.position) < dist(pos, best.position):
            best = b
    return best


def free_cell_near(center):
    """A building-free cell a few steps from `center` (for a new Flying Station)."""
    occ = occupied_cells()
    for radius in (3, 4, 5, 6):
        for dx, dy in DIRS:
            cell = (center[0] + dx * radius, center[1] + dy * radius)
            if cell not in occ:
                return cell
    return (center[0] + 3, center[1] + 3)


# --------------------------------------------------------------------------- #
# movement: fly toward a target, but charge first if we couldn't get home
# --------------------------------------------------------------------------- #
def approach(r, target):
    """Fly `r` toward `target`, topping up energy first when the round trip would
    strand it. Returns "arrived" when already on the target cell (the caller then
    acts), or "moving" when a flight was issued (toward the target OR a pad)."""
    target = (round(target[0]), round(target[1]))
    if r.cell == target:
        return "arrived"

    pads = charging_pads()
    if pads and r.energy is not None:
        home = min(dist(target, p) for p in pads)                 # target -> nearest pad
        if r.energy < dist(r.position, target) + home + ENERGY_MARGIN:
            pad = min(pads, key=lambda p: dist(r.position, p))
            if r.cell == pad:
                r.charge()
            else:
                r.move_to(*pad)
            return "moving"

    r.move_to(target[0], target[1])
    return "moving"


def explore(r):
    """Fly into the fog to reveal new ground; each trip picks a fresh heading."""
    n = r.memory.get("trip", 0) + 1
    r.memory["trip"] = n
    dx, dy = DIRS[(sum(map(ord, r.id)) + n) % len(DIRS)]
    x, y = r.position
    approach(r, (x + dx * 6, y + dy * 6))


# --------------------------------------------------------------------------- #
# reusable jobs (each returns True when it has claimed this robot's turn)
# --------------------------------------------------------------------------- #
def fund_sites(r):
    """Carry materials to the nearest half-built construction site (a mine or a
    Flying Station). Empty robots fetch the exact recipe from Storage; loaded
    robots drop what a site still needs. Sites self-complete once supplied."""
    sites = [b for b in buildings.all() if b.status == "constructing" and b.construction]
    if not sites:
        return False
    inv = r.inventory

    if inv.ore or inv.metal:                       # loaded -> deliver to a site that wants it
        target = None
        for b in sites:
            need_o = b.construction.required["ore"] - b.construction.delivered["ore"]
            need_m = b.construction.required["metal"] - b.construction.delivered["metal"]
            if (inv.ore and need_o > 0) or (inv.metal and need_m > 0):
                if target is None or dist(r.position, b.position) < dist(r.position, target.position):
                    target = b
        if target is None:
            return False                           # nothing here needs our load -> let haul use it
        if approach(r, target.position) == "moving":
            return True
        r.drop()                                   # site takes only its recipe; excess stays with us
        return True

    # empty -> fetch the nearest site's remaining recipe from a stocked Storage
    site = min(sites, key=lambda b: dist(r.position, b.position))
    need_o = site.construction.required["ore"] - site.construction.delivered["ore"]
    need_m = site.construction.required["metal"] - site.construction.delivered["metal"]
    src = nearest_building(r.position, "storage",
                           want=lambda b: b.storage.ore >= need_o and b.storage.metal >= need_m)
    if src is None:
        return False                               # Storage can't fund it yet -> do other work
    if approach(r, src.position) == "moving":
        return True
    r.pick_up(need_o, need_m)
    return True


def haul(r, resource, target, amount):
    """Pick up `resource` from the nearest stocked mine and drop it at `target`
    (the Base quest or a station being stocked). Returns True while busy."""
    have = r.inventory.ore if resource == "ore" else r.inventory.metal
    if have > 0:
        if approach(r, target) == "moving":
            return True
        r.drop(r.inventory.ore, r.inventory.metal)
        return True
    mine = nearest_building(r.position, "mining",
                            want=lambda b: b.spot and b.spot.resource == resource
                            and b.storage.ore + b.storage.metal > 0)
    if mine is None:
        return False
    if approach(r, mine.position) == "moving":
        return True
    take = max(1, min(CARRY, amount))
    r.pick_up(take, 0) if resource == "ore" else r.pick_up(0, take)
    return True


def grow(r, base):
    """OPTIONAL fleet growth: after level 1, ONE robot (the 'grow lead') builds a
    Flying Station, stocks it, and produces one extra robot. Everyone else keeps
    feeding the quest, so growth never starves it. State is in the durable store."""
    if (base.level or 1) < 2 or store.get("grow_done"):
        return False
    if store.get("grow_lead") is None:
        store["grow_lead"] = r.id
    if r.id != store.get("grow_lead"):
        return False

    stations = buildings.stations()
    if not stations:
        if not store.get("station_placed"):        # place the site once (fund_sites builds it)
            site = free_cell_near(base.position)
            world.build("flying_station", *site)
            store["station_placed"] = True
            r.log(f"growth: placing a Flying Station at {site}")
        return False                               # let fund_sites/haul drive this robot
    st = stations[0]
    if st.status != "active":
        return False                               # still under construction -> fund_sites handles it
    if st.storage.ore >= ROBOT_COST[0] and st.storage.metal >= ROBOT_COST[1]:
        st.build_robot(1)                          # stocked -> manufacture a robot
        store["grow_done"] = True
        r.log("growth: station stocked — building a new robot")
        return True
    want = "ore" if st.storage.ore < ROBOT_COST[0] else "metal"
    need = ROBOT_COST[0] - st.storage.ore if want == "ore" else ROBOT_COST[1] - st.storage.metal
    return haul(r, want, st.position, need)


# --------------------------------------------------------------------------- #
# the controller — one handler drives the whole fleet
# --------------------------------------------------------------------------- #
@on.idle
def act(e):
    r = robots[e.robot_id]
    base = buildings.base
    if base is None or r.position is None:
        return

    # 1) ENERGY GUARD — if we're low and away from a pad, get to one and charge.
    pads = charging_pads()
    if pads and r.energy is not None:
        pad = min(pads, key=lambda p: dist(r.position, p))
        if r.energy <= dist(r.position, pad) + ENERGY_MARGIN:
            r.charge() if r.cell == pad else r.move_to(*pad)
            return

    # 2) PLACE MINES — make sure an ore mine AND a metal mine exist. Placing a
    #    site is free and robot-independent; a later step funds it.
    for res in ("ore", "metal"):
        if res in mine_resources():
            continue
        spot = free_spot(r.position, res)
        if spot:
            world.build("mining", *spot)
            r.log(f"placing a {res} mine at {spot}")
            return
        # No known spot for this resource yet -> we'll explore below to find one.

    # 3) FUND SITES — carry materials to any half-built mine or station.
    if fund_sites(r):
        return

    # 4) GROW (optional) — one robot expands the fleet once we're past level 1.
    if grow(r, base):
        return

    # 5) HAUL THE QUEST — carry mine output to the Base to climb levels. Deliver
    #    a carried load first, then pick up more of whatever the quest needs most.
    req, prog = base.quest.required, base.quest.progress
    need_o, need_m = req["ore"] - prog["ore"], req["metal"] - prog["metal"]
    inv = r.inventory
    if inv.ore or inv.metal:
        if (inv.ore and need_o > 0) or (inv.metal and need_m > 0):
            if approach(r, base.position) == "moving":
                return
            r.drop()                               # Base accepts up to the requirement
            return
        bank = nearest_building(r.position, "storage")   # Base wants none -> bank it for kits
        if bank and approach(r, bank.position) != "moving":
            r.drop()
        return
    for res in sorted(("ore", "metal"), key=lambda x: -(need_o if x == "ore" else need_m)):
        need = need_o if res == "ore" else need_m
        if need > 0 and haul(r, res, base.position, need):
            return

    # 6) Nothing useful to do -> explore and reveal more of the map.
    explore(r)
