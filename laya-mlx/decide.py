"""Turn a PokéSurvivor snapshot into a Laya question, then shield the answer.

The model only sees a short English description. The game already marks which
directions are free of walls and imminent hazards; if Laya's top choice is not
one of those, the next-highest safe label is used instead.

    python laya-mlx/decide.py
"""

import math

# Phrases, not single words: Laya follows these more reliably.
MOVE_LABELS = [
    "stand still",
    "move north",
    "move north-east",
    "move east",
    "move south-east",
    "move south",
    "move south-west",
    "move west",
    "move north-west",
]

# Level-up stat titles are Spanish in the game. The sentence glosses them.
STAT_GLOSS = {
    "Vigor": "more max HP",
    "Potencia": "more damage",
    "Carrera": "faster movement",
    "Reflejos": "shorter move cooldown",
    "Coraza": "less damage taken",
    "Síntesis": "HP regeneration",
    "Imán": "pick up experience from farther away",
    "Aprendizaje": "more experience",
    "Baya Zidra": "heal a large amount now",
}

_BEARINGS = [
    "north", "north-east", "east", "south-east",
    "south", "south-west", "west", "north-west",
]


def load_agent():
    """Local Apple Silicon checkpoint. Downloads once into the Hugging Face cache."""
    import laya_mlx as laya
    return laya.load("aac6fef/laya-mlx")


def _bearing(dx, dy):
    # Game Y grows south, so north is negative dy.
    ang = math.atan2(float(dx), -float(dy))
    i = int((ang + math.pi / 8) % (2 * math.pi) / (math.pi / 4))
    return _BEARINGS[i]


def _range(dist):
    dist = float(dist)
    if dist < 36:
        return "on top of you"
    if dist < 80:
        return "very close"
    if dist < 160:
        return "close"
    if dist < 280:
        return "nearby"
    return "in the distance"


def _gloss(label, detail):
    hint = STAT_GLOSS.get(label)
    bits = [b for b in (hint, detail) if b]
    return (label + (" (" + ". ".join(bits) + ")" if bits else ""))


def describe(snap):
    """One or two sentences. Numbers stay out except HP percent and a few clocks."""
    hp = float(snap.get("hp") or 0)
    mx = float(snap.get("maxHp") or 1) or 1
    pct = round(100 * hp / mx)
    parts = [
        "You are %s in a Pokémon survival run. "
        "HP %d percent. Level %d. Time %d seconds."
        % (snap.get("mon") or "your Pokémon", pct, int(snap.get("level") or 1), int(snap.get("time") or 0))
    ]
    if snap.get("liquid"):
        parts.append("You are standing in %s, which hurts you." % snap["liquid"])
    foes = snap.get("enemies") or []
    if not foes:
        parts.append("No wild Pokémon are close.")
    else:
        bits = []
        for e in foes[:4]:
            kind = "boss " if e.get("boss") else ""
            bits.append(
                "a %s%s %s to the %s"
                % (kind, e.get("name") or "wild Pokémon", _range(e.get("dist") or 0),
                   _bearing(e.get("dx") or 0, e.get("dy") or 0))
            )
        parts.append("Threats: " + "; ".join(bits) + ".")
    hazards = snap.get("hazards") or []
    if hazards:
        bits = []
        for h in hazards[:3]:
            bits.append(
                "a %s zone %s to the %s, striking in %.1f seconds"
                % (h.get("kind") or "danger", _range(h.get("dist") or 0),
                   _bearing(h.get("dx") or 0, h.get("dy") or 0), float(h.get("delay") or 0))
            )
        parts.append("Danger on the ground: " + "; ".join(bits) + ".")
    else:
        parts.append("No danger zone is about to hit.")
    safe = [s for s in (snap.get("safe") or []) if s in MOVE_LABELS]
    parts.append("Safe directions: " + ", ".join(safe) + "." if safe else "No direction is safe.")
    phase = snap.get("phase")
    if phase in ("levelup", "item", "bond"):
        intro = {
            "levelup": "Choose one level-up reward.",
            "item": "Choose which held item to keep.",
            "bond": "Choose which moves to fuse.",
        }[phase]
        lines = []
        for i, c in enumerate(snap.get("cards") or [], 1):
            lines.append("Option %d: %s." % (i, _gloss(c.get("label") or "", c.get("detail") or "")))
        parts.append(intro + " " + " ".join(lines))
    return " ".join(parts)


def questions_for(snap):
    phase = snap.get("phase")
    if phase in ("levelup", "item", "bond"):
        labels = []
        for c in snap.get("cards") or []:
            lab = c.get("label") or ""
            if lab and lab not in labels:
                labels.append(lab)
        if not labels:
            return None
        return {
            "pick": {
                "type": "choice",
                "instructions": "Which option should the Pokémon take?",
                "criteria": labels,
            }
        }
    return {
        "move": {
            "type": "choice",
            "instructions": "Which way should the Pokémon move to stay alive?",
            "criteria": MOVE_LABELS,
        },
        "stay_hit": {
            "type": "noul",
            "instructions": "Is the Pokémon about to be hit if it stays where it is?",
        },
    }


# Same 40px lookahead as safeDirs in bridge.js. Game Y grows south.
_STEP = (18, 40)
_VECS = {
    "move north": (0, -1),
    "move north-east": (1, -1),
    "move east": (1, 0),
    "move south-east": (1, 1),
    "move south": (0, 1),
    "move south-west": (-1, 1),
    "move west": (-1, 0),
    "move north-west": (-1, -1),
}


def steer(snap):
    """Keep walking. Dodge wild Pokémon, stay off walls and trees, then collect.

    ponytail: eight fixed directions, rescored every snapshot. A real pathfinder
    would be the upgrade if it starts trapping itself in crowds.
    """
    safe = []
    for s in snap.get("safe") or []:
        if s in _VECS and s not in safe:
            safe.append(s)
    if not safe:
        return "stand still"
    foes = snap.get("enemies") or []
    loot = snap.get("pickups") or []
    blocks = snap.get("blocks") or []
    pr = float(snap.get("r") or 14)
    heading = snap.get("heading") or ""
    best, best_score = safe[0], -1e18
    for label in safe:
        vx, vy = _VECS[label]
        length = math.hypot(vx, vy) or 1
        ux, uy = vx / length, vy / length
        score = 18 if label == heading else 0
        for ahead in _STEP:
            cx, cy = ux * ahead, uy * ahead
            for e in foes:
                dist = math.hypot(float(e.get("dx") or 0) - cx, float(e.get("dy") or 0) - cy)
                gap = float(e.get("r") or 14) + pr + 28
                if dist < gap:
                    score -= 1200 + (gap - dist) * 10
                elif dist < 150:
                    score -= (150 - dist) * (3 if e.get("boss") else 1.6)
            for b in blocks:
                bx, by = float(b.get("dx") or 0), float(b.get("dy") or 0)
                dist = math.hypot(bx - cx, by - cy)
                gap = float(b.get("r") or 16) + pr + 48
                if dist < gap:
                    score -= (gap - dist) * 12
            for p in loot:
                px, py = float(p.get("dx") or 0), float(p.get("dy") or 0)
                now = math.hypot(px, py)
                if now > 300:
                    continue
                kind = p.get("kind")
                w = 2 if kind == "xpBig" else 1.5 if kind == "coin" else 1
                score += w * (now - math.hypot(px - cx, py - cy)) * 4
        if score > best_score:
            best, best_score = label, score
    return best


def shield(choice, probabilities, safe):
    """Keep `choice` when it is safe. Otherwise the safe label with the next-highest probability.

    An empty safe list cannot be improved, so the raw choice is returned.
    ponytail: ties break by label text; a scored shortlist would need the model's own order.
    """
    allowed = []
    for s in safe or []:
        if isinstance(s, str) and s not in allowed:
            allowed.append(s)
    if not allowed:
        return choice
    if choice in allowed:
        return choice
    ranked = sorted((probabilities or {}).items(), key=lambda kv: (-float(kv[1]), str(kv[0])))
    for label, _p in ranked:
        if label in allowed:
            return label
    return allowed[0]


SWORDS = "swords-dance"
HARDEN = "harden"


def _moves(snap):
    return snap.get("moves") or []


def _best_attack(moves):
    atks = [m for m in moves if m.get("kind") != "buff"]
    if not atks:
        return None
    return max(atks, key=lambda m: (m.get("lvl") or 0, bool(m.get("active")), m.get("name") or ""))


def _using(moves):
    """The attack in the slot that is firing. Buff-only slot falls back to its lead."""
    active = [m for m in moves if m.get("active") and m.get("kind") != "buff"]
    if active:
        return max(active, key=lambda m: (m.get("lvl") or 0, bool(m.get("lead"))))
    leads = [m for m in moves if m.get("active") and m.get("lead")]
    return leads[0] if leads else _best_attack(moves)


def _find(cards, pred):
    for i, card in enumerate(cards):
        if pred(card):
            return i
    return None


def _attack_cards(cards, moves):
    by_id = {m.get("id"): m for m in moves}
    out = []
    for i, card in enumerate(cards):
        m = by_id.get(card.get("move"))
        if m and m.get("kind") != "buff":
            out.append((i, m))
        elif card.get("fresh") and card.get("kind") not in ("buff", "", None):
            out.append((i, card))
    return out


def level_choice(cards, moves):
    """Upgrade the move in use, else take Swords Dance or Harden, else a different-type 4th attack."""
    using = _using(moves)
    if using:
        hit = _find(cards, lambda c: c.get("move") == using.get("id") and (c.get("upgrade") or "→" in (c.get("label") or "")))
        if hit is not None:
            return hit
    for mid in (SWORDS, HARDEN):
        hit = _find(cards, lambda c, mid=mid: c.get("move") == mid)
        if hit is not None:
            return hit
    slots = {m.get("slot") for m in moves}
    primary = _best_attack(moves)
    if len(slots) >= 3 and primary:
        hit = _find(
            cards,
            lambda c: c.get("fresh") and c.get("kind") not in ("buff", "", None) and c.get("type") and c.get("type") != primary.get("type"),
        )
        if hit is not None:
            return hit
    return None


def bond_choice(cards, moves, bond):
    """Anchor the most upgraded attack, fuse Swords Dance then Harden, then a different-type attack."""
    if not bond:
        return None
    by_id = {m.get("id"): m for m in moves}
    mode, step = bond.get("mode"), bond.get("step") or 1
    group = [by_id[i] for i in (bond.get("group") or []) if i in by_id]
    have = {m.get("id") for m in group}

    def named(mid):
        return _find(cards, lambda c, mid=mid: c.get("move") == mid)

    def strongest(pred=None):
        opts = [(i, m) for i, m in _attack_cards(cards, moves) if not pred or pred(m)]
        if not opts:
            return None
        return max(opts, key=lambda im: (im[1].get("lvl") or 0, im[1].get("name") or ""))[0]

    if mode == "pair" and step == 1:
        return strongest()
    if mode == "pair" and step == 2:
        for mid in (SWORDS, HARDEN):
            hit = named(mid)
            if hit is not None:
                return hit
        first = by_id.get(bond.get("first"))
        ftype = first.get("type") if first and first.get("kind") != "buff" else None
        if ftype:
            hit = strongest(lambda m: m.get("type") != ftype)
            if hit is not None:
                return hit
        return strongest()
    for mid in (SWORDS, HARDEN):
        if mid not in have:
            hit = named(mid)
            if hit is not None:
                return hit
    anchor = next((m for m in group if m.get("kind") != "buff"), None)
    if anchor and (len(group) >= 3 or {SWORDS, HARDEN} <= have):
        hit = strongest(lambda m: m.get("type") != anchor.get("type"))
        if hit is not None:
            return hit
    return strongest()


def build_choice(snap):
    cards = snap.get("cards") or []
    if not cards:
        return None
    phase = snap.get("phase")
    if phase == "levelup":
        return level_choice(cards, _moves(snap))
    if phase == "bond":
        return bond_choice(cards, _moves(snap), snap.get("bond"))
    return None


def decide(agent, snap):
    forced = build_choice(snap)
    if forced is not None:
        cards = snap.get("cards") or []
        return {
            "action": "card",
            "index": forced,
            "label": cards[forced].get("label"),
            "shielded": False,
        }
    phase = snap.get("phase")
    if phase not in ("levelup", "item", "bond"):
        if phase != "playing":
            return {"action": "wait"}
        return {"action": "move", "label": steer(snap), "shielded": False}
    qs = questions_for(snap)
    if not qs:
        return {"action": "wait"}
    result = agent.predict(describe(snap), qs)
    answers = result["answers"]
    if "pick" not in answers:
        return {"action": "wait"}
    ans = answers["pick"]
    labels = qs["pick"]["criteria"]
    label = shield(ans.get("choice"), ans.get("probabilities"), labels)
    index = labels.index(label) if label in labels else 0
    return {
        "action": "card",
        "label": label,
        "index": index,
        "shielded": label != ans.get("choice"),
    }


def _check():
    got = shield(
        "move north",
        {"move north": 0.7, "move west": 0.2, "stand still": 0.1},
        ["move west", "stand still"],
    )
    assert got == "move west", got
    assert shield("move west", {"move north": 0.9, "move west": 0.1}, ["move west"]) == "move west"
    text = describe({
        "phase": "playing",
        "hp": 40, "maxHp": 90, "level": 2, "time": 12,
        "enemies": [{"name": "Pidgey", "dist": 50, "dx": 0, "dy": -50, "boss": False}],
        "hazards": [],
        "safe": ["move west", "stand still"],
    })
    assert "Pidgey" in text and "north" in text and "move west" in text, text
    ember = {"id": "ember", "name": "Ascuas", "kind": "projectile", "type": "fire", "lvl": 3, "slot": 0, "lead": True, "active": True, "group": 1}
    water = {"id": "water-gun", "name": "Pistola Agua", "kind": "projectile", "type": "water", "lvl": 1, "slot": 1, "lead": True, "active": False, "group": 1}
    swords = {"id": "swords-dance", "name": "Danza Espada", "kind": "buff", "type": "normal", "lvl": 1, "slot": 2, "lead": True, "active": False, "group": 1}
    harden = {"id": "harden", "name": "Fortaleza", "kind": "buff", "type": "normal", "lvl": 1, "slot": 3, "lead": True, "active": False, "group": 1}
    moves = [ember, water, swords, harden]
    up = [
        {"label": "Danza Espada", "move": "swords-dance", "kind": "buff", "type": "normal", "fresh": True},
        {"label": "Ascuas", "move": "ember", "kind": "projectile", "type": "fire", "upgrade": True},
    ]
    assert level_choice(up, moves) == 1
    assert level_choice(up[:1], moves) == 0
    fourth = [
        {"label": "Lanzallamas", "move": "flamethrower", "kind": "cone", "type": "fire", "fresh": True},
        {"label": "Pistola Agua", "move": "water-gun", "kind": "projectile", "type": "water", "fresh": True},
    ]
    assert level_choice(fourth, [ember, swords, harden]) == 1
    pair = [
        {"label": "Pistola Agua", "move": "water-gun", "kind": "projectile", "type": "water"},
        {"label": "Ascuas", "move": "ember", "kind": "projectile", "type": "fire"},
    ]
    assert bond_choice(pair, moves, {"mode": "pair", "step": 1, "group": [], "first": ""}) == 1
    step2 = [
        {"label": "Pistola Agua", "move": "water-gun", "kind": "projectile", "type": "water"},
        {"label": "Danza Espada", "move": "swords-dance", "kind": "buff", "type": "normal"},
        {"label": "Fortaleza", "move": "harden", "kind": "buff", "type": "normal"},
    ]
    assert bond_choice(step2, moves, {"mode": "pair", "step": 2, "group": [], "first": "ember"}) == 1
    assert bond_choice(step2, moves, {"mode": "add", "step": 1, "group": ["ember", "swords-dance"], "first": ""}) == 2
    flame = {"id": "flamethrower", "name": "Lanzallamas", "kind": "cone", "type": "fire", "lvl": 1, "slot": 4, "lead": True, "active": False, "group": 1}
    off = [
        {"label": "Lanzallamas", "move": "flamethrower", "kind": "cone", "type": "fire"},
        {"label": "Pistola Agua", "move": "water-gun", "kind": "projectile", "type": "water"},
    ]
    assert bond_choice(off, moves + [flame], {"mode": "add", "step": 1, "group": ["ember", "swords-dance", "harden"], "first": ""}) == 1
    ways = [s for s in MOVE_LABELS if s != "stand still"]
    assert steer({"safe": ways, "r": 14, "enemies": [{"dx": 0, "dy": -50, "r": 16}], "pickups": []}) == "move south"
    assert steer({"safe": ways, "r": 14, "enemies": [], "pickups": [{"kind": "coin", "dx": 100, "dy": 0}]}) == "move east"
    assert steer({"safe": ways, "r": 14, "heading": "move west", "enemies": [], "pickups": []}) == "move west"
    dodged = steer({
        "safe": ways, "r": 14,
        "enemies": [{"dx": 36, "dy": 0, "r": 16}],
        "pickups": [{"kind": "coin", "dx": 80, "dy": 0}],
    })
    assert dodged in ("move west", "move south-west", "move north-west"), dodged
    assert steer({"safe": ["stand still"], "enemies": [], "pickups": []}) == "stand still"
    assert steer({
        "safe": ways, "r": 14, "enemies": [], "pickups": [],
        "blocks": [{"dx": 30, "dy": 0, "r": 16}],
    }) == "move west"
    print("ok")


if __name__ == "__main__":
    _check()
