# SimCode city — robot controller (Python)

This repo is the **brain of one SimCode city**. `main.py` is a single Python program
that controls *all* the robots in your city in the **Robot City Builder** game. You don't
click to place buildings — **code is the only way to influence the world**. Push to this
repo's default branch and the platform hot-reloads your code into the running city; the
robots immediately act on the new program and you watch the city evolve at your city's
live page.

> This is a **user code repo**, not the platform. You only write the controller. The
> `simcode` SDK, the world, the rules, and the robots all come from the platform.

## ⚡ Test locally BEFORE you push (do this every iteration)

Pushing to see the result is slow. There's a **local simulator** that runs your
`main.py` against an offline copy of the game engine, **seeded from your city's
CURRENT state**, so you can check "does this actually work if I push it *now*?" in
seconds — and only push once it behaves. **Install it and use it on every change.**

```bash
pip install "git+https://github.com/oduvan/simcode-robocity-python-tools"

robocity-sim run main.py          # tests THIS city's current state (auto-detected, no token)
robocity-sim run main.py --json   # machine-readable (parse summary + feed)
```

Run it **inside this repo** — a city's live state is public, so **no token needed**;
it auto-detects which city this repo is and fetches its current state. Your `main.py`
runs **unchanged**. Read the
`SUMMARY`: `robots destroyed` should be **0**, and `ore/metal mined` + `buildings`
should grow if the city is developing. A live run is *approximate* (a quick "does it
work now" check, not a perfect sim) — real edge cases surface after you push. Only
push after a local run looks right. See that repo's `CLAUDE.md` for full usage.

> **Check your code with `robocity-sim run main.py` — NOT `python main.py`.** The
> `simcode` SDK is **not** a pip package (the platform provides it at runtime), so
> running your file directly just fails on `import simcode`. `robocity-sim` provides
> the SDK locally *and* runs your code against the engine, so you verify **behaviour**,
> not just that it imports. It's the one reliable way to check a controller.

## How it works (the model)

- **One script, whole fleet.** `main.py` controls every robot, addressed by **id**.
- **Event-driven, async.** You register handlers; the game dispatches events; you react by
  issuing **commands** (intents). Data in → intents out. You never hold a live game object.
- **State is read fresh** from the world on each event — `robots[...]`, `buildings`, `world`
  reflect the current tick when your handler runs.
- **Serial per robot.** Events for one robot arrive one at a time; a robot runs one command
  at a time (a new command replaces the active one).
- **No manifest, no config.** The repo is just this script (+ optional `lib/`). Language is
  chosen when the city is created; the entry is always `main.py`; the world and starting
  robots are defined by the game module.

## The game you're playing (Robot City Builder)

Goal of the reference module: **raise the Base's level**. The Base sets a **quest** (an amount
of ore + metal); deliver it and the Base **levels up** to a harder quest — endlessly. Your
**highest Base level is your score.**

> **This starter does NOT play the game.** It only keeps the robots alive and flies them
> around to explore the map. Building the winning loop below is **your** job — that's the point
> of a starter. Grow `main.py` from the bare explorer it ships with.

The loop you'll build toward:

```
pick up a kit from the starting Storage → fly to a resource spot →
  place a Mining site (world.build) + drop the kit to build it → the mine digs itself →
  haul its ore/metal to the Base to fill the quest → Base LEVELS UP → repeat, harder
  (and: build a Flying Station to make more robots; recharge on any pad to keep flying)
```

- **Robots start EMPTY.** There's no free kit — a robot carries nothing until it picks
  something up. Your capital is a **Storage building pre-placed next to the Base**, stocked
  with **30 ore / 15 metal**; robots `pick_up` from it to get building materials.
- **The world is endless & continuous.** Robots have **float** `(x, y)` positions and **fly**
  in straight lines from any point to any point, ignoring terrain and each other (no
  pathfinding, multiple robots may share a spot). They interact with a building by their
  **rounded cell** (`r.cell`). Flying **spends energy** (∝ distance); run the battery to zero
  **mid-flight and the robot is destroyed** — its cargo vanishes. Recharge by landing on a
  **charging pad** (the **Base** or a **Flying Station**) and calling `r.charge()`.
- **Resources:** `ore` and `metal`, found at finite **spots** (a spot yields one or the other).
  A **Mining building mines autonomously** into its own storage — there is no `mine` command;
  a robot only **picks up** the output and **hauls** it away.
- **Buildings:**
  - **Base** (pre-placed, one) — the **quest hub** and a **charging pad**. `drop` ore/metal on
    it to progress the current quest; meet it and it **levels up**. You **cannot** `pick_up`
    from the Base (its store is the quest accumulator only).
  - **Storage** (2×2 hub) — a big buffer robots `pick_up` from and `drop` into. The starting
    one holds your capital; you can build more with `world.build("storage", …)`.
  - **Mining** — placed on a live resource spot; auto-mines its resource into a small capped
    store that robots `pick_up` from.
  - **Flying Station** — a **charging pad** *and* the **robot factory**: stock it (`drop`
    ore/metal), then call `station.build_robot(n)` to produce robots there.
- **Everything except the Base is built autonomously:** place a site with
  `world.build(type, x, y)`, robots **`drop`** resources to fulfil the recipe, and the site
  **self-completes** once supplied — no connect step, no robot labor.
- **Recipes:** Mining `6 ore + 3 metal`, Storage `3 ore`, Flying Station `4 ore + 2 metal`; a
  Flying Station spends `12 ore + 6 metal` from its own store per robot it builds.
- **Same map for everyone.** The module fixes the world seed, so *every* city of this type
  starts from the **identical canonical map** — the only variable is your code. It's a contest
  of whose program climbs to the highest Base level.

## SDK reference

```python
from simcode import on, robots, buildings, world, store
```

### Subscribe to events — `@on.<event>`
Each event carries `e.robot_id`. Common events and their extra fields:

| Event | Fields | Fires when |
| --- | --- | --- |
| **`idle`** | — | **a robot has no command and needs one** — after any command completes, or right after spawn. Re-fires every few ticks while it stays free (not every tick). **This is the main hook: handle it, decide, issue the next command.** |
| `spawn` | — | a robot enters the world (or your code reloads). |
| `arrived` | `position` | a `move_to` flight reached its target. |
| `blocked` | `reason` | a move/action couldn't complete (e.g. `no_station`). |
| `construction_started` | `building_id`, `type` | a `world.build(...)` placed a site. |
| `resource_delivered` | `building_id`, `ore`, `metal` | a `drop` onto a site/store landed. |
| `construction_complete` | `building_id`, `type` | a site finished building (now active). |
| `spot_depleted` | `building_id` | a Mining building's resource spot ran out (no `robot_id`). |
| `storage_full` | `building_id` | a building's storage is full (no `robot_id`). |
| `inventory_full` | — | a robot can't carry more. |
| `robot_produced` | `robot_id` | a **Flying Station** finished a new robot. |
| `robot_destroyed` | `position`, `reason` | a robot ran out of energy **mid-flight** — gone, cargo lost. |
| `charge_complete` | — | a robot on a charging pad finished charging (battery full). |
| `quest_updated` | `level`, `requirements{ore,metal}` | the Base's current quest — at start and after each level-up (`building_id`, no `robot_id`). |
| `base_level_up` | `level`, `quest{ore,metal}` | the Base cleared its quest and **leveled up** (`building_id`, no `robot_id`). |
| `message` | `from`, `payload` | another robot messaged this one. |

The cleanest controller is built around **`idle`**: it fires exactly when a robot is free,
so you don't poll and you don't have to chain every completion event by hand. The starter is
essentially one `@on.idle` handler that reads the robot's live state and issues its next move.
The other events are there when you want their payload (e.g. `arrived.position`). Discovery
happens **by flying** — a robot reveals a radius (~5) around itself as it moves; to explore,
just `move_to` a point in the fog. There is no separate reveal command.

`subscribe(event, handler, once=False)` / `unsubscribe(...)` register at runtime too.

### Command a robot — `r = robots[id]`
A command tells one robot to do one thing. The robot can run **only one at a time** — issuing
a new command replaces the current one. Timed commands (`move_to`, `charge`) finish over
several ticks and fire a completion event; instant ones (`pick_up`, `drop`) resolve right
away. **Either way, when the robot is free again it fires `idle`** — so you rarely need the
specific completion events; just react to `idle`. Placing a building is a **world** command,
`world.build(...)`, not bound to a robot.

| Call | What it does | Completes with |
| --- | --- | --- |
| `r.move_to(x, y)` | **Fly** in a straight line to float `(x, y)`, ignoring terrain/other robots. Spends energy with distance; reveals the map (radius ~5) as it goes — this is how you explore. | `arrived` / `blocked` / `robot_destroyed` |
| `world.build("mining"\|"storage"\|"flying_station", x, y)` | Place a self-building construction **site** at `(x, y)`. `mining` must be on a live resource spot; the Base isn't buildable. **Not** bound to a robot. | `construction_started` / `blocked` |
| `r.pick_up(ore=…, metal=…)` | Grab resources from the building **on your cell** into your inventory (up to carry capacity). No args = take everything that fits. Instant. | resolves, then `idle` |
| `r.drop(ore=…, metal=…)` | Release your inventory into the building/site **on your cell** — supply a build site, or feed the Base/Storage. No args = drop all. Instant. | `resource_delivered` |
| `r.charge()` | Charge on the **Flying Station on your cell**; holds the robot until the battery is full. | `charge_complete` / `blocked` (`no_station`) |
| `r.send(target_id, payload)` | Send a message to another robot. | the peer gets a `message` event |
| `r.cancel()` | Abort the current command; the robot goes free. | `idle` |
| `r.log("…")` | Write a line to the city log (for debugging your code). | — |

**Position-based:** `pick_up`, `drop`, and `charge` act on whatever building/site is on the
robot's **current (rounded) cell** (`r.cell`). So to haul, `move_to` the mine, `pick_up`, then
`move_to` the Base and `drop`; to recharge, `move_to` a Flying Station then `charge()`. Mining
and construction are **autonomous**, so there are no robot-driven mining, build-wiring,
site-placing, or single-step-move commands — robots only fly, haul, and charge.

### The Base — the quest hub — `b = buildings.base`
There's one Base; reach it via `buildings.base`. It **isn't built or commanded** — you feed it
and read its objective:
- **Feed it:** robots `drop(ore=…, metal=…)` on the Base's cell. Its store is the **quest
  accumulator**, capped per-resource at the requirement (excess stays on the robot). You
  **cannot `pick_up` from the Base.** It also doubles as a **charging pad** (`r.charge()`).
- **Read the objective:** `buildings.base.level` (current level, starts at 1) and
  `buildings.base.quest` — `.required` and `.progress`, each `{ore, metal}` (progress =
  min(delivered, required)). Deliver the required ore+metal and the Base **levels up** to the
  next, harder quest. Subscribe to `quest_updated` / `base_level_up` to react.

### Grow the fleet — Flying Stations — `buildings.stations()`
Robots are built at a **Flying Station** (not the Base). Build one with
`world.build("flying_station", x, y)`, stock it, then command it:

| Call | What it does |
| --- | --- |
| `station.build_robot(n=1)` | Queue `n` robots at **this** station. Each consumes `12 ore + 6 metal` from the station's own store and takes time; each finished one spawns **empty** at the station and fires `robot_produced` + its first `idle`. Waits if the store is short. |
| `station.cancel()` | Clear this station's production queue. |

Get a station handle from `buildings.stations()` (or `buildings.of_type("flying_station")`);
each exposes `.storage` (its production store — `drop` ore/metal here to fuel building) and
`.production` (`.active`, `.progress`, `.queued`). You **cannot `pick_up` from a station** (its
store is production-only).

### Read the world (read-only handles)
You never hold a live object — these read **fresh** state each time your handler runs.

- **Robots:** `robots[id]`, `robots.all()`, `robots.of_type(t)`. A robot handle exposes
  `r.id`, `r.type`, `r.position` → **float** `(x, y)`, `r.cell` → the **rounded** `(x, y)` used
  for position-based actions, `r.facing`, `r.state`
  (`idle`/`moving`/`charging`/`hauling`/`blocked`), `r.command` (what it's doing),
  `r.energy` (battery, 0…cap), `r.inventory` (`.ore`, `.metal`, `.free`, `.capacity`,
  `.is_full`), `r.here` (`.terrain`, `.spot`, `.building` — what's on its cell),
  `r.nearest(kind=…|type=…)`, and `r.memory` (a per-robot dict you can write to).
- **Buildings:** `buildings[id]`, `buildings.all()`, `buildings.of_type(t)`, `buildings.base`,
  `buildings.stations()`. A building handle exposes `.type`
  (`base`/`mining`/`storage`/`flying_station`), `.position`, `.footprint` (w, h),
  `.status` (`constructing`/`active`), `.storage` (`.ore`/`.metal`/`.capacity`/`.free`),
  `.spot` (Mining: `.resource`, `.remaining` — the building auto-mines into its storage),
  `.level` + `.quest` (Base: `.quest.required` / `.quest.progress`, each `{ore, metal}`),
  `.production` (Flying Station: `.active`, `.progress`, `.queued`), `.construction` (while
  building: `.required`, `.delivered`, `.progress` — sites self-complete, so **no `connected`
  field**).
- **World:** `world.tick`; `world.size` (bounding box of the **discovered** region — a viewport
  hint, not a fixed extent), `world.origin`, `world.endless` (`True`); `world.spots()` — the
  resource spots you've **discovered so far** (each with `.position`, `.spot.resource`,
  `.spot.remaining`); `world.cells()` — the revealed tiles. The world is **endless**, generated
  lazily as robots fly into the fog. `world.build(type, x, y)` places a construction site.
- **`store`** — a city-wide dict for your own state that survives across events.

## Constraints — read before editing

- **Sandbox.** Your code runs in a restricted sandbox. You may `from simcode import …` and use
  a normal-Python subset, but **no file/network/process access, no arbitrary imports, no
  `eval`/`exec`/`open`**. Keep helpers in `lib/` and import them normally.
- **Handlers must be fast.** Each handler has a tight CPU/time budget per invocation. Do a
  little work and return; don't loop for long or block.
- **State:** module-level globals persist while the process runs but **reset on a code reload**.
  For state that must survive a push, use `store` (city-wide) or `r.memory` (per robot).
- **Determinism:** don't rely on wall-clock or randomness; the world is seeded and replayable.
- **The SDK is provided** by the platform — do **not** `pip install simcode` or vendor it.

## Working in this repo with Claude Code

- The thing to improve is the **strategy** in `main.py` (and `lib/`). The world is fixed, so
  better code = a better city.
- You can't run the engine locally; iterate by reading the logic carefully and by checking the
  live city + logs after a push (or via the platform's MCP tools).
- High-leverage improvements over the starter: bootstrap **both** an ore mine and a metal mine
  quickly (the quest needs both), keep hauling to the Base to **climb levels**, and when a
  mine's spot runs dry (`spot_depleted`) build a **replacement** so production never stalls.
  Recharge robots **before** they run dry (a robot that runs out of energy mid-flight is
  destroyed and its cargo lost) — build **Flying Stations** near your mining frontier as extra
  charging pads *and* to grow the fleet with `station.build_robot(...)`. Add **Storage** as a
  buffer. Watch the tension: a Flying Station's store pays for robots, and the Base's store
  pays for quests — balance building robots against leveling up.
- **The game is purely event-driven — do NOT use an `on.tick` polling loop.** Build around
  **`on.idle`**: it fires precisely when a robot needs a command. The golden rule: **every
  handler must issue the robot's next command** (fly / haul / `world.build` / `charge`), so a
  robot is never left idle with no future event to wake it. (And since `idle` re-fires while a
  robot stays free, a robot is never permanently stuck.) If a code path would leave a robot
  with nothing to do, fly it into unexplored ground instead — flying reveals new map. That
  single discipline is what keeps the city growing without any polling.

## Report back — turn each session into a forum post

The platform has a small **community forum** (browse it at `/forum`; posts are written through the
platform's **MCP** tools). Treat reporting as part of the job: **when a working session wraps up —
or while you monitor the city grow — analyse what happened and file a forum post.** Good reports
are how the game gets better.

**First, interview your human — thoroughly.** Before posting, ask targeted questions and keep
asking follow-ups:
- What were they trying to achieve, and did it happen?
- What surprised or confused them (in the API, the docs, or the sim)?
- What was missing — and **what would they want next** (features, quests, mechanics)? Capture
  *ideas*, not just bugs.

**Then file a forum post per distinct issue or idea** via the MCP forum tools, in this format so a
post is easy to reproduce and close:
- **kind:** `bug`, `idea` (features/quests go here), or `question`.
- **city (required):** at least the **city slug** (plus its type / relevant state if useful).
- **for a `bug`:** what you did → **what you saw** → **what you expected to see** → repro steps →
  how often it happens.
- **for an `idea`/quest:** the proposal + why it matters + how it would play.

Never file a bare "it did not work" — always **slug + saw + expected + repro**. Report both real
bugs *and* future ideas: the forum is where the roadmap comes from.
