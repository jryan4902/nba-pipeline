# Raw payloads

What the Phase 0 spike actually saves, based on the files in `data/raw/`. Sample: 3 runs, 12 games
(2026-03-15: 7 regular-season games, pulled twice; 2026-10-05: 5 preseason games). Every file is the
endpoint's response unchanged, with two top-level keys: `meta` (`version`, `request`, `time`) and the payload.

## Scoreboard (`ScoreboardV3`)

Path: `data/raw/scoreboard/game_date=<YYYY-MM-DD>/<pulled_at>.json`

```
scoreboard > games[] > homeTeam | awayTeam > periods[]
scoreboard > games[] > gameLeaders | teamLeaders > homeLeaders | awayLeaders
```

| List | Grain |
|---|---|
| `games[]` | one game on that date |
| `games[].homeTeam` / `awayTeam` | one team in a game (an object, not a list) |
| `games[].homeTeam.periods[]` | one quarter (or OT) for one team: `period`, `periodType`, `score` |

IDs and dates:
- `scoreboard.gameDate` (str, `"2026-03-15"`): the only real date field. The box scores have none.
- `games[].gameId` (str, `"0022500976"`)
- `games[].homeTeam.teamId` / `awayTeam.teamId` (int, `1610612760`)
- `gameLeaders.*.personId` (int): one leader per team (with `points`, `rebounds`, `assists`), not a full roster.
- `games[].gameCode` (`"20260315/MINOKC"`), `gameTimeUTC`, `gameEt`, `gameStatus` (int; `3` = Final), `gameLabel` (`"Preseason"` or `""`).
- No season field. The season is only encoded in `gameId` (see Notes).

Also per team: `wins`, `losses` (record after this game), `score`, `seed`, `timeoutsRemaining`, `inBonus`.

## Traditional box score (`BoxScoreTraditionalV3`)

Path: `data/raw/boxscore_traditional/game_date=<date>/<gameId>/<pulled_at>.json`

```
boxScoreTraditional > homeTeam | awayTeam > players[] > statistics
boxScoreTraditional > homeTeam | awayTeam > statistics | starters | bench
```

| Object | Grain |
|---|---|
| `homeTeam` / `awayTeam` | one team in a game |
| `players[]` | one player on that team's roster for the game, **including DNP and inactive players** |
| `team.statistics` / `starters` / `bench` | team totals and starter/bench subtotals |

IDs: `boxScoreTraditional.gameId` (str), `homeTeamId` / `awayTeamId` (int), `homeTeam.teamId` (int),
`players[].personId` (int). Player info fields: `firstName`, `familyName`, `nameI`, `playerSlug`, `position`, `jerseyNum`, `comment`.

| Field | Type | Player (C.Bryant) | Team (SAS) |
|---|---|---|---|
| `minutes` | str | `"17:39"` | `"240:00"` |
| `fieldGoalsMade` / `Attempted` | int | `0` / `4` | `42` / `101` |
| `fieldGoalsPercentage` | float | `0.0` | `0.416` |
| `threePointersMade` / `Attempted` | int | `0` / `2` | `15` / `40` |
| `threePointersPercentage` | float | `0.0` | `0.375` |
| `freeThrowsMade` / `Attempted` | int | `3` / `4` | `17` / `19` |
| `freeThrowsPercentage` | float | `0.75` | `0.895` |
| `reboundsOffensive` / `Defensive` / `Total` | int | `0` / `0` / `0` | `15` / `29` / `44` |
| `assists`, `steals`, `blocks`, `turnovers` | int | `0`, `1`, `0`, `0` | `28`, `16`, `6`, `7` |
| `foulsPersonal` | int | `0` | `16` |
| `points` | int | `3` | `116` |
| `plusMinusPoints` | float | `-6.0` | `13.0` |

`starters` and `bench` have the same fields as the team totals, minus `plusMinusPoints`.

## Advanced box score (`BoxScoreAdvancedV3`)

Path: `data/raw/boxscore_advanced/game_date=<date>/<gameId>/<pulled_at>.json`

```
boxScoreAdvanced > homeTeam | awayTeam > players[] > statistics
boxScoreAdvanced > homeTeam | awayTeam > statistics
```

Same grain, IDs and player info fields as traditional, but no `starters` or `bench`. All stats except `minutes` are float.

| Field | Player (C.Bryant) | Team (SAS) |
|---|---|---|
| `minutes` (str) | `"17:39"` | `"240:00"` |
| `offensiveRating` / `defensiveRating` / `netRating` | `97.3` / `113.5` / `-16.2` | `116.0` / `104.0` / `12.0` |
| `estimatedOffensiveRating` / `Defensive` / `Net` | `97.3` / `113.5` / `-16.2` | `580.0` / `520.2` / `59.8` |
| `assistPercentage`, `assistToTurnover`, `assistRatio` | `0.0`, `0.0`, `0.0` | `0.667`, `4.0`, `18.9` |
| `offensiveReboundPercentage` / `defensive…` / `rebound…` | `0.0` / `0.0` / `0.0` | `0.333` / `0.667` / `0.476` |
| `turnoverRatio` | `0.0` | `7.0` |
| `effectiveFieldGoalPercentage`, `trueShootingPercentage` | `0.0`, `0.26` | `0.49`, `0.53` |
| `usagePercentage`, `estimatedUsagePercentage` | `0.13`, `0.13` | `1.0`, `0.975` |
| `pace`, `estimatedPace`, `pacePer40` | `100.62`, `100.62`, `83.85` | `99.5`, `19.9`, `82.92` |
| `possessions` | `37.0` | `100.0` |
| `PIE` | `-0.014` | `0.592` |
| `estimatedTeamTurnoverPercentage` | (not present) | `7.0` |

## Surprises

- **`minutes` is `"MM:SS"`, not ISO** (`PT34M12.00S` doesn't appear anywhere). Formats seen: `"17:39"`, `"9:51"`, `"240:00"`, and `""` for players who didn't play.
- **DNP stats are zeros, not nulls.** A DNP or inactive player has `minutes: ""`, every stat `0` / `0.0`, and the reason in `comment`.
  Comments seen: `DNP - Coach's Decision`, `DND - Injury/Illness`, `DND - Rest`, `Inactive - Coach's Decision`,
  `Inactive - Injury/Illness`. 115 of 568 player rows are DNP. To find them, test `minutes == ""`; a 0 doesn't mean the player didn't play.
- **Box scores have no nulls at all.** The only null in any file is `scoreboard…homeTeam/awayTeam.inBonus`.
- **IDs:** `gameId` is a **string** with leading zeros; `teamId` and `personId` are **integers**. Same in all three endpoints.
- **No starter flag.** `position` is filled in for 5 players per team in most games, but 6 or 7 in 5 of 38 team rows, so it can't identify starters.
- **`starters.minutes` always equals `bench.minutes`** (e.g. `130:27` / `130:27`) and they don't add up to 240. Points do add up (starters + bench = team).
- **Team `estimated*` ratings look wrong** in the advanced file (`estimatedOffensiveRating: 580.0`, `estimatedPace: 19.9`). Use the non-estimated versions.
- **`gameEt` ends in `Z` but is Eastern time** (`13:00:00Z` while `gameTimeUTC` is `17:00:00Z`).
- **`meta.time` isn't the pull time.** It was identical across both Mar 15 pulls and earlier than tip-off. Use `pulled_at`.
- **Present in one endpoint only:** `starters` / `bench` (traditional); `estimatedTeamTurnoverPercentage` (advanced, team only); `plusMinusPoints` (traditional; missing from starters/bench).
- **Preseason rosters are much bigger:** 19–21 players per team, vs 11–15 in the regular season.

## How the files join

- **Scoreboard → box scores:** `scoreboard.games[].gameId` = `boxScore*.gameId`, which is also the `<gameId>` folder in the path.
  The game date comes from `scoreboard.gameDate` (or the `game_date=` folder). The box scores don't carry it.
- **Teams:** `games[].homeTeam.teamId` = `boxScore*.homeTeamId` = `boxScore*.homeTeam.teamId` (same for away). Matched in all 12 games.
- **Team → player rows:** players are nested under their team, so there's no team ID on a player row.
  Attach the team ID from the parent `homeTeam`/`awayTeam` when flattening.
- **Traditional ↔ advanced:** same `personId` set, in the same order, for every team in all 12 games. Join on `(gameId, personId)`.
- **Row counts, game `0022500976` (MIN @ OKC):** 2 team rows per file; 13 OKC players + 14 MIN players = 27 player rows per file
  (3 DNP). Across the sample: 11–15 players per team (regular season), 19–21 (preseason).

## Notes for Phase 2

Proposed natural keys for the raw tables, with `pulled_at` (from the file name) as the version column:

| Table | Key |
|---|---|
| raw games (scoreboard) | `(game_id, pulled_at)` |
| raw team rows (trad / adv) | `(game_id, team_id, pulled_at)` |
| raw player rows (trad / adv) | `(game_id, player_id, pulled_at)` |

In this sample nothing breaks these keys: no duplicate `personId` within a game, no player on both teams, no null IDs.
Things to watch:
- **Store `game_id` as text.** Casting it to an integer drops the leading `00` and breaks the joins.
- **Duplicate pulls are expected.** Mar 15 was pulled twice and the payloads are identical. Downstream models
  should pick the latest `pulled_at` per key, not assume one row per game.
- **`pulled_at` is per run, not per file.** Every file in a run shares one timestamp, so it versions runs, not individual requests.
- **No season column.** Get it from `game_id`: characters 1–3 give the type (`002` regular, `001` preseason) and characters 4–5 give the
  season's start year (`25` = 2025-26). Preseason and regular-season rows will share tables unless you filter on this.
- **DNP rows will be in the player tables.** Keep them (with `comment`), but convert `minutes = ""` to NULL or 0 when typing the column.
- **Not yet observed:** overtime games (extra `periods[]` rows), traded players, games that aren't Final. Recheck the keys once a
  backfill covers these.
