# My SimCode City (Python)

This repo controls a city in **SimCode — Robot City Builder**. `main.py` is one Python
program that drives the whole robot fleet; **push to the default branch and the platform
hot-reloads** it into your live city.

- **Edit `main.py`** to change how your robots behave (fly out, place mines, haul, charge, grow).
- **Push** → your city updates in real time at its live page.
- No setup, no manifest, no dependencies to install — the `simcode` SDK is provided by the
  platform at runtime.

New here? Open **[`CLAUDE.md`](CLAUDE.md)** — it explains the game, the full SDK (events +
commands + read model), the rules, and the sandbox constraints. It's written so
[Claude Code](https://claude.com/claude-code) can help you write better robot code.

```
main.py        # your controller (the only thing that runs)
lib/           # optional helper modules main.py imports
CLAUDE.md      # the SDK + game reference
```

## Test it locally before you push

There's a local tool that runs your `main.py` against your city's **current state**,
so you can check "does this actually work if I push it now?" in seconds:

```bash
pip install "git+https://github.com/oduvan/simcode-robocity-python-tools"
robocity-sim run main.py          # tests THIS city's current state (no token needed)
```

Run it inside this repo — a city's live state is public, so no token is needed; it
auto-detects your city from the git remote. See [`CLAUDE.md`](CLAUDE.md) for full usage.

Have fun — the map is the same for everyone, so it's all about your code.
