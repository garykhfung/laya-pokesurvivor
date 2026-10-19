# Laya plays PokéSurvivor

A local controller for a checkout of [PokéSurvivor](https://github.com/garykhfung/pokesurvivor). It opens the game on this machine and plays a solo guest run. Nothing is uploaded, and this repo does not modify the game.

Two folders, two projects:

| Folder | What it is |
| --- | --- |
| `laya-mlx/` | This controller. It is the git project. |
| `pokesurvivor/` | Your copy of the game, with its own `.git`. It is listed in `.gitignore`, so commits here never include it. |

## Run

Needs an Apple Silicon Mac, Python 3.11 or newer, and Chrome. Use a separate virtualenv so this does not change any other MLX install.

```bash
python3 -m venv .venv
.venv/bin/pip install -r laya-mlx/requirements.txt
.venv/bin/python laya-mlx/play.py
.venv/bin/python laya-mlx/play.py pikachu
.venv/bin/python laya-mlx/play.py 25
```

That serves `pokesurvivor/dev.html` at `http://127.0.0.1:8765` and opens it in a throwaway Chrome profile. The page asks which Pokémon to play and whether it is shiny, then which permanent stats to start with. Those stats are not bought with coins. A name on the command line only fills the first field. While the run is going, a panel on the right shows each decision: the question, the situation, and whether a rule or Laya answered. The Laya checkpoint downloads once into the Hugging Face cache.

`python laya-mlx/decide.py` checks the card and movement rules without loading the model.

## How it plays

Movement is a fixed rule, not the model. The Pokémon keeps walking and steps away before a wild Pokémon touches it. It stays clear of walls and trees so its shots are not swallowed, and it walks out of poison, sticky, and blast traps and out of enemy areas on the ground. When the attack it is using is short range, it closes until that attack can connect, then holds that distance. It also walks toward nearby experience and coins. A Meowth coin rush comes first, then a shiny, then a void. Every 3 seconds it compares its feet to where they were. If it is still within 120px, it takes a random safe direction and holds it until the next check. If its feet stay inside a 64px patch for 5 seconds and none of those events is up, it also drops that heading and walks out.

Level-up and bonding are also fixed:

1. Upgrade the attack that is firing. A buff is taken only when it is already fused to an attack, and a buff-only slot is never the one in use. After four moves are fused, take a permanent stat instead (regeneration and experience first) and keep using that fused attack.
2. On a bond, the most upgraded attack goes first, then Swords Dance, then Harden. The fourth member is an attack of a different type.
3. For the first 10 minutes, hold the Lucky Egg when it is offered. After that, hold Choice Band, then Life Orb. Any other item is left to Laya.

## Update the game

The game checkout is independent. From `pokesurvivor/`:

```bash
git checkout main
git pull
```

Do that whenever you want a newer PokéSurvivor. This controller keeps using `dev.html` from that folder.
