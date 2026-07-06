"""SimCode city controller — a MINIMAL starting point.

This starter does one thing on purpose: it keeps the robots alive and flies them
around to **explore the map**. It does NOT mine, build, haul, or climb Base levels —
that is for YOU to add.

Read CLAUDE.md for the whole game (the goal, the buildings, the full SDK API) and
grow this controller from here. The idea is simple: `@on.idle` fires whenever a robot
needs its next order, so decide what the robot should do and issue one command.
"""

from simcode import on, robots

# Compass headings. A robot advances one heading per trip (kept in its memory) so the
# fleet fans out across the map instead of re-treading a single line into the fog.
DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]

EXPLORE_HOP = 5    # world units to fly per exploration step
CHARGE_MARGIN = 15  # spare battery to keep on top of the trip home


@on.idle
def act(e):
    r = robots[e.robot_id]

    # Stay alive: a robot that runs its battery to zero mid-flight is destroyed, so
    # head back to the Base to recharge WHILE there's still enough energy to reach it.
    # The Base sits at the origin and doubles as a charging pad. (Distance-aware, not a
    # fixed threshold — otherwise a robot can wander further than it can fly back from.)
    if r.energy is not None:
        x, y = r.position
        home = (x * x + y * y) ** 0.5  # distance to the Base at (0, 0)
        if r.energy < home + CHARGE_MARGIN:
            if r.cell == (0, 0):
                r.charge()
            else:
                r.move_to(0, 0)
            return

    # Otherwise explore: fly a short hop along a rotating heading. Flying reveals the
    # map (~5 cells around the robot), so this is how you uncover resource spots.
    n = r.memory.get("hop", 0) + 1
    r.memory["hop"] = n
    dx, dy = DIRS[n % len(DIRS)]
    x, y = r.position
    r.move_to(x + dx * EXPLORE_HOP, y + dy * EXPLORE_HOP)
