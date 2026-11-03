# Plan: Wins Above Replacement (WAR) for draft Pokémon

Status: proposal, nothing built yet.
Goal: replace the Draft tab's "value" with one number per Pokémon, in **wins**, that measures
how much that Pokémon added to its team's chances of winning compared with a freely available
replacement. Every part of it should come from the replays. Where judgment is needed, it should
be one named setting that is easy to find, not a hidden constant.

---

## 1. What's wrong with the current "value"

`value = (HP bars of direct damage dealt + 0.1 × turns on field) / team games` (`index.html`, `D_TURN_W`).

| Problem | Effect |
| --- | --- |
| Only **direct** move damage counts. | Across the 49 games so far, 80 HP bars of indirect damage (sand 22, burn 13, poison 13, Stealth Rock 12, Leech Seed 5, Rocky Helmet/Iron Barbs/Rough Skin…) get no credit. That's about 16% of all damage. Tyranitar's sand, Will-O-Wisp users and Stealth Rock setters are undervalued. |
| **Healing and defense** count for nothing. | 500+ heal events (Wish, Recover, drain, Leech Seed, Leftovers) and all damage *taken* are ignored. A Pokémon that faints turn 1 and one that tanks four hits score the same, apart from the turn bonus. |
| **Every HP bar is worth the same.** | 50% of the opponent's last healthy Pokémon in a close game counts the same as chip damage in a blowout. Overkill and clean-up damage are inflated. |
| The **0.1 per turn** is arbitrary. | Stalling, or sitting in on a Protect loop, earns value whether or not it helped. |
| **Support** (Tailwind, Trick Room, Follow Me, Intimidate, screens, Fake Out, status) is invisible except through turns on field. | Support picks are undervalued. |
| No **baseline**. | "1.2 value" has no meaning without comparing to the league. "vs round" exists but is round-relative only. |
| No **luck** or **sample-size** handling. | One crit-fuelled game can top the board. |

## 2. Core idea: Win Probability Added (WPA) → WAR

1. Fit a **win-probability model**: for any moment in a game, P(side wins | current state).
2. Replay every game event by event. Each event that changes the state changes win probability
   (ΔWP). Give that ΔWP to the Pokémon responsible (+ for the side it helped, − for the side it hurt).
3. A Pokémon's **WPA** is the sum of its ΔWP credits. It is measured in wins: across a team,
   one game's WPA sums to +0.5 for the winner and −0.5 for the loser (WP goes from 50% to 100%
   or 0%), so it reconciles exactly with the standings.
4. **WAR** = WPA − what a replacement-level pick would have produced in the same playing time.

Why this beats a weighted sum of stats: weights come from what actually wins games in *this*
league. Damage that swings games counts for more than damage in decided games. Defense, healing
and indirect damage all fit in the same units without hand-picked weights.

## 3. The win-probability model

### State features (per side, as differences p1 − p2)
Start small, since there are only about 50 games (~700 turns, ~2,500 HP-changing events):

| Feature | Notes |
| --- | --- |
| `alive` | Pokémon not fainted, out of the 6 brought (unrevealed ones count as healthy). |
| `hp` | Sum of remaining HP fractions. |
| `impaired` | Count of alive Pokémon that are asleep, frozen or paralyzed (burn/poison already show up through `hp` over time). Optional; keep only if validation supports it. |

The model is symmetric, `WP(p1) = σ(w · (s₁ − s₂))` with no intercept, so `WP(p1) = 1 − WP(p2)`
and both sides start at exactly 50%.

Later, with more games: a turn-number interaction (the same lead matters more late), field
effects (Tailwind / Trick Room turns left), and hazards on each side.

### Fitting
- Take a snapshot before and after each HP / faint / status event, labelled with the game result.
- Weight each game equally, since long games would otherwise dominate.
- Logistic regression in pure Python (Newton's method on 2–3 features; no numpy, so the GitHub
  Action stays dependency-free). Add a light L2 penalty.
- Refit on every build. Optionally freeze the weights per season once there are enough games, so
  old WAR numbers don't drift every week.

### Validation (script prints these; keep them in `data/war_meta.json`)
- Leave-one-game-out Brier score and log loss, compared with the "hp only" and "alive only" models.
- Calibration table (predicted 0–10%, 10–20%… vs actual).
- Sanity check: at the start WP = 50%; after a 2-for-0 trade the side ahead should be around 75–85%.

## 4. Credit assignment rules

Every event's ΔWP (from the acting side's view) goes to **one actor on each side**: + to the
helper, − to the victim. Anything with no clear actor goes to that side's **team bucket**, so team
totals still reconcile.

| Event (replay line) | + credit | − charge |
| --- | --- | --- |
| Direct damage (`-damage`, no `[from]`) | Attacker (last `move`) | Pokémon that took it |
| KO | Same as damage; the `alive` term makes the jump big | Fainted Pokémon |
| Spread move hitting 2 targets | Attacker gets each hit separately | Each target |
| Burn / poison tick | Whoever inflicted the status (track the `-status` source; Toxic Spikes → setter; Flame/Toxic Orb → self, i.e. no opponent credit) | Victim |
| Sandstorm / hail tick | Weather setter (`-weather … [of]` or the last Sandstorm/Hail move user) | Victim |
| Stealth Rock / Spikes on switch-in | Hazard setter (`-sidestart` source) | Victim |
| Leech Seed drain | Seeder (both the damage and its heal) | Victim |
| Rocky Helmet, Rough Skin, Iron Barbs | Holder | Attacker that made contact |
| Life Orb, recoil, confusion self-hit, Belly Drum/Substitute HP | Opponent team bucket | The Pokémon itself |
| Healing: Recover/Roost/Rest, Leftovers, Black Sludge, Sitrus, drain moves, Rain Dish | The healed Pokémon | Opponent team bucket |
| Healing by an ally: Wish, Heal Pulse, Pollen Puff-like, Lunar Dance/Healing Wish | The healer | Opponent team bucket |
| Sleep / freeze / paralysis inflicted (if `impaired` is in the model) | Inflicter | Victim |
| Self-KO moves (Explosion, Memento, Healing Wish) | KOs and damage as normal | The user is charged for its own faint |
| Forfeit / timer | Remaining WP swing to both team buckets | — |

**Why charge the victim:** it rewards bulk and good switching (taking 30% from a hit instead of
90% loses less WP), and it penalises Pokémon that get picked off for free. It's the same as
baseball WPA charging the pitcher for what the batter gains.

**Setting:** `VICTIM_SHARE` (default 1.0). Lower it if we decide being targeted reflects the
opponent's choices more than the victim's.

### Support and enabling credit (phase 2)
Effects that change no HP but enable teammates get an **assist share** of the ally WPA they enable:

| Effect | Assist credit |
| --- | --- |
| Tailwind, Trick Room | Share `ASSIST` (e.g. 25%) of positive ally WPA while it's active, to the setter |
| Reflect / Light Screen | Same idea, applied to damage the allies take while it's up (reduced charge) |
| Follow Me / Rage Powder | Attacks redirected onto the user: the user takes the charge, and the would-be target's partner gets nothing |
| Helping Hand | Share of the boosted attack's ΔWP |
| Intimidate, stat drops, Fake Out flinch, Taunt, Encore | First pass: none. Revisit if the support picks clearly read low |

Assist credit is taken *out of* the enabled Pokémon's credit, not added on top, so totals still
reconcile. This is the most judgment-heavy part. It should be its own WAR component so people
can see it separately.

### Luck-adjusted variant
`scripts/luck.py` already finds crits, misses, secondary effects and full paralysis. Option:
**xWAR**, which credits expected rather than actual results:
- Crit damage: scale the ΔWP of a crit hit by 1/2 (Gen 5 crits do 2×) plus the 1/16 crit chance.
- Misses: give the attacker p(hit) × the average ΔWP of that move's hits, not 0.
- Secondary effects (burn, para, flinch): credit p × value, not 0 or 1.

Show WAR as the main number and xWAR in a tooltip or column. The gap is "luck".

## 5. Replacement level and the final number

- **Opportunity** = turns on the field. Being brought and sitting in the back is a separate,
  smaller opportunity.
- **Replacement rate** = WPA per turn of a replacement-level pick. Proposal: the average WPA per
  turn of Pokémon from the **last two draft rounds** (NFE picks) that were brought. That's what a
  team can always get. Alternative: the 20th percentile of all brought Pokémon. Make it a setting
  and show it in the methodology note.
- `WAR = WPA − replacement_rate × turns_on_field`
- Also show **WAR / game** (per team game) and **WAR / brought game**.
- Not brought: 0 WAR, still listed separately (as now). Team-preview "threat value" from a
  Pokémon that's never brought isn't measurable from replays. Say so.

### Sample size
- **Shrunk rate** for ranking: empirical-Bayes shrink of WAR per turn toward the league mean
  (prior strength ≈ the turns of a typical game or two). Raw totals stay visible.
- **Uncertainty**: bootstrap over games (resample games, refit WP, recompute) to give a ±
  interval for each Pokémon. Shown in the tooltip.

### Context (later, optional)
- **Opponent strength**: scale credits by how good the opponent team is (their season team WAR).
  Hold off until there are 2+ rounds of games.

## 6. Breakdown shown on the site

One headline number, plus components that add up to it so it isn't a black box:

| Component | From |
| --- | --- |
| Offense | Direct damage and KOs |
| Indirect | Status, weather and hazard damage it caused |
| Defense | Charges for damage taken and fainting (negative, but less negative for bulky Pokémon) |
| Healing | Self and ally healing |
| Support | Assist credit (phase 2) |
| Replacement | − replacement_rate × turns |
| **= WAR** | |

## 7. Implementation steps

1. **Parser: event stream with actors** (`scripts/replay.py`)
   - Track HP, alive and status for all 12 Pokémon per game. Add an ordered `events` list:
     `{turn, kind, target, actor, frac_before, frac_after, status_change}`.
   - Track sources: who caused each status, the weather setter, hazard setters per side, the
     Leech Seed seeder, contact for Rocky Helmet-type damage, and Wish/heal-move users.
   - Count `-heal` events (currently they only update HP).
   - Keep the existing per-Pokémon counters unchanged so the current tabs don't move.
2. **WP model** (`scripts/war.py`, new)
   - `state_vector(state)`, `fit(games)` (Newton logistic regression), `wp(weights, state)`.
   - `credit_game(game, weights)` → per-Pokémon component totals plus team buckets.
   - Replacement level, shrinkage, bootstrap intervals.
   - A check that each game's credits sum to ±0.5 per side (fail the build otherwise).
3. **Build** (`scripts/build.py`)
   - Write `data/war.json` (season, per player+Pokémon: components, WAR, WAR/game, xWAR,
     interval), `data/gamewar.json` (per game per Pokémon, for game pages and filters), and
     `data/war_meta.json` (weights, replacement rate, validation numbers).
4. **Site** (`index.html`)
   - Draft tab: replace `value` with WAR/game in "Value by pick", rounds, tiers, classes and
     steals/busts. Steals/busts become WAR vs the round's average WAR, and draft classes become
     total WAR.
   - Pokémon tab: add WAR, WAR/game and xWAR columns. The tooltip shows the component breakdown.
   - Optional: a per-game "biggest swings" list (top ΔWP events, e.g. "Garchomp's Earthquake
     KO, +22% WP"). It falls straight out of the event stream and makes the metric easy to
     believe.
   - Rewrite the methodology note: what WP is, the credit rules, replacement level, settings.
5. **Checks**: `scripts/check_war.py`, runnable locally and in the Action:
   reconciliation, symmetry (swap p1/p2 → negated credits), and a few hand-verified games.

## 8. How we'll know it's better

- **Reconciles**: team WAR + team bucket + replacement adjustments line up with actual wins
  above average.
- **More stable**: split-half reliability (odd vs even weeks) of WAR/game vs current value/game.
  A better metric of real skill should correlate more strongly across halves.
- **Predicts better**: summed season-to-date WAR of the six brought Pokémon predicts the next
  game's winner better than summed current value.
- **Passes the eye test**: league members look over the top and bottom 10 and the support picks.

## 9. Phases

| Phase | Scope |
| --- | --- |
| 1 | Event stream, 2-feature WP model, damage/KO/indirect/heal credit, replacement level, WAR on the Draft tab |
| 2 | Status (`impaired`) feature, support/assist credit, component breakdown on the Pokémon tab, biggest-swing list |
| 3 | xWAR (luck-adjusted), bootstrap intervals, shrinkage, opponent adjustment |

## 10. Open questions for the league

1. Charge the victim fully (`VICTIM_SHARE = 1`), or split it? Recommendation: 1.0.
2. Replacement level: last-two-rounds average, or 20th percentile? Recommendation: last two rounds.
3. Headline number: raw WAR, or luck-adjusted xWAR? Recommendation: WAR headline, xWAR alongside.
4. Assist share for Tailwind / Trick Room (default 25%)? Or leave support out until phase 2 is checked?
5. Refit the WP model weekly, or freeze it per season once there are about 40+ games?
