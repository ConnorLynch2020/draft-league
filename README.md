# Draft League Stats

Stats site for our Pokémon Showdown draft league (Gen 5 Doubles). Everything on the site is
calculated from the replay links in `replays.txt`.

## Adding a game

1. Open `replays.txt` on github.com and click the pencil icon.
2. Paste the replay link on a new line, e.g. `https://replay.pokemonshowdown.com/gen5doublescustomgame-2705123456`.
3. Commit. The **Update stats and publish** action runs and the site refreshes in a minute or two
   (watch it under the *Actions* tab).

The week and game number are worked out from the two players and `config/schedule.json`. If a game
doesn't fit (a rematch, a make-up game), put the week after the link: `<link> 4`.

Replays have to be uploaded as **public** on Showdown (the "Upload and share replay" button).

### No link? Use the downloaded replay file

If nobody saved the link but someone has the replay `.html` file (Showdown's "Download replay"
button), that works too:

1. On github.com open the `replays` folder (or create it), click **Add file → Upload files** and
   upload the `.html` file.
2. Add a line to `replays.txt` with the file's path, e.g. `replays/week4-game2.html` (a week number
   after it works the same as for links).
3. Commit. The site's replay link for that game opens the uploaded file.

## If the action fails

Open the failed run and read the last lines. The usual causes:

| Message | Fix |
| --- | --- |
| `Showdown account 'xyz' isn't in config/players.json` | Add `"xyz": "Player Name"` to `config/players.json`. Players can have several accounts. |
| `isn't on the schedule` | Add the week number after the link, or fix `config/schedule.json`. |
| `Couldn't fetch ...` | The replay is private or the link is wrong. |
| `Replay file ... doesn't exist` / `has no battle log` | Check the path in `replays.txt`, and that the file is the one Showdown's Download replay button saved. |
| `has no winner recorded` | The game wasn't finished. Add it once it is. |

Fix the file and commit again; the action re-runs.

## How it works

- `scripts/build.py` downloads each replay's log (cached in `cache/`, so history survives even if
  Showdown deletes a replay), parses it (`scripts/replay.py`), and rewrites `data/*.json`.
- `scripts/luck.py` scores every random event (crits, accuracy, secondary effects, full paralysis,
  thaws, confusion) against its odds. Hidden information (Scope Lens, Serene Grace, Compound Eyes,
  etc.), speed ties and damage rolls aren't visible in replays, so they aren't included.
- `scripts/impact.py` works out **Impact**, the value stat on the Draft and Pokémon tabs: HP bars
  each Pokémon added (damage, set-up damage, healing) or kept off its team (Intimidate, screens,
  Protect, immunities, lost turns, speed control). **Impact+** is Impact per game brought against
  the league average, where 100 is average. See `docs/war-plan.md`.
- `index.html` + `dash-shim.js` render the page from `data/*.json`. Sprites for Pokémon that aren't
  in `sprites1.js` / `sprites2.js` load from Showdown's sprite server.
- `scripts/ref/*.json` is move and type reference data (Gen 5), refreshed with
  `python3 scripts/refresh_refdata.py`.

## Running locally

    python3 scripts/build.py            # fetch new replays and rebuild data/
    python3 scripts/build.py --offline  # only use cached replays
    python3 -m http.server              # then open http://localhost:8000

Python 3.9+ and no dependencies.

## One-time setup

1. Create a **public** GitHub repo and push this folder to `main`.
2. Settings → Pages → Build and deployment → Source: **GitHub Actions**.
3. Run the *Update stats and publish* workflow once from the Actions tab.
