"""Turn a PokéSurvivor snapshot into a move or a card tap.

play.py posts each snapshot from bridge.js here. Movement is a fixed rule, and
so are the usual level-up, bond, and item picks. Laya is asked only when a card
is on screen and no rule takes it. The model sees one English paragraph from
describe(); shield() then keeps its answer inside the labels that were offered.
The run is trying to last until the Judgment at 30 minutes. A dash is never the
attack that is kept: Quick Attack, Aerial Ace, Flame Charge, and U-turn carry
the Pokémon into the horde. What those rules do is written up in README.md
under "How it plays".

Running this file checks those rules and does not load the model:

    python laya-mlx/decide.py

A snapshot is the dict bridge.js builds. The fields this file reads:

    phase     playing, levelup, item, bond, or anything else (wait)
    hp, maxHp, level, time, mon, liquid
    x, y, r, heading
    safe      direction labels the game already marked clear
    nudge     a forced heading when the feet have barely moved for 3 seconds
    trail     recent feet, used to leave a patch that has not moved
    enemies, shots, hazards, traps, blocks, pickups, goals
    boss      a boss is alive; a Meowth and a shiny wait unless a rift is open
    tune      {dodge, range, item} from the side panel, for this run
    moves     known attacks and buffs, with slot, group, active, reach
    cards     the options on screen; empty while playing
    bond      {mode, step, group, first} during a bond

decide() returns one of:

    {"action": "move", "label": <direction>, "shielded": False, "note": ...}
    {"action": "card", "index": <0-based>, "label": ..., "shielded": bool, "note": ...}
    {"action": "wait"}

note is what the on-screen panel shows. source is "rule" or "laya", plus the
question, the scene, the result, and raw when shield changed Laya's label.
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
    """The card question, or None when the screen has no labels.

    decide() calls this only after the card rules pass. Movement is not asked.
    """
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


# Under this, the old "stay 150px away" kite keeps the hit from landing.
_SHORT = 180


def _tune(snap):
    tune = snap.get("tune") or {}
    return tune if isinstance(tune, dict) else {}


def _attack_reach(snap):
    """Shortest live attack in the firing slot. 0 when every shot already reaches."""
    reaches = []
    for m in snap.get("moves") or []:
        if not m.get("active") or m.get("kind") == "buff":
            continue
        reach = float(m.get("reach") or 0)
        if 0 < reach < _SHORT:
            reaches.append(reach)
    return min(reaches) if reaches else 0.0


def _close_score(ux, uy, foes, reach, pr):
    """Step toward the nearest foe until it is inside `reach`, and stop at contact.

    The probe is one tick long, and shorter when a full step would walk into them.
    """
    if not foes:
        return 0.0
    near = min(foes, key=lambda e: math.hypot(float(e.get("dx") or 0), float(e.get("dy") or 0)))
    score = 0.0
    for e in foes:
        dx, dy = float(e.get("dx") or 0), float(e.get("dy") or 0)
        now = math.hypot(dx, dy)
        body = float(e.get("r") or 14) + pr + 4
        step = 16.0
        if e is near and dx * ux + dy * uy > 0 and now > body:
            step = min(step, now - body)
        dist = math.hypot(dx - ux * step, dy - uy * step)
        if dist < body:
            score -= 1200 + (body - dist) * 10
        elif e is near and dist <= reach:
            score += 500
        elif e is near and dist < now:
            score += (now - dist) * 50
    return score


# Whole trail inside this radius of its center counts as stuck.
_STUCK_R = 64
_STUCK_S = 5


def _escape(snap, safe):
    """Leave a patch the feet have not left for 5 seconds, on a different heading.

    ponytail: one centroid. Opposite of the failed heading when that center is underfoot.
    """
    pts = [p for p in (snap.get("trail") or []) if isinstance(p, dict)]
    if not any(float(p.get("dt") or 0) >= _STUCK_S for p in pts):
        return None
    xs = [float(p.get("x") or 0) for p in pts]
    ys = [float(p.get("y") or 0) for p in pts]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    if max(math.hypot(x - mx, y - my) for x, y in zip(xs, ys)) > _STUCK_R:
        return None
    ax = float(snap.get("x") or 0) - mx
    ay = float(snap.get("y") or 0) - my
    heading = snap.get("heading") or ""
    if math.hypot(ax, ay) < 12:
        hx, hy = _VECS.get(heading, (0, 0))
        ax, ay = -float(hx), -float(hy)
    best, best_score = None, -1e18
    for label in safe:
        vx, vy = _VECS[label]
        length = math.hypot(vx, vy) or 1
        score = vx / length * ax + vy / length * ay
        if label == heading:
            score -= 1000
        if score > best_score:
            best, best_score = label, score
    return best


def _circle_depth(dx, dy, cx, cy, rad):
    return rad - math.hypot(float(dx) - cx, float(dy) - cy)


def _beam_depth(h, cx, cy, pr):
    ang = float(h.get("angle") or 0)
    ca, sa = math.cos(ang), math.sin(ang)
    px, py = cx - float(h.get("dx") or 0), cy - float(h.get("dy") or 0)
    along = px * ca + py * sa
    if along < -pr or along > float(h.get("len") or 0) + pr:
        return 0.0
    side = abs(-px * sa + py * ca)
    half = float(h.get("w") or 0) / 2 + pr * 0.6
    return half - side if side <= half else 0.0


def _touch(snap, cx, cy, pr):
    """How far this point sits inside a Pokémon or a hostile shot.

    A shot is sampled along the next third of a second. Enemy shots move at
    about 190px/s, so three samples overlap a body-sized circle.
    """
    depth = 0.0
    for e in snap.get("enemies") or []:
        d = _circle_depth(e.get("dx") or 0, e.get("dy") or 0, cx, cy, float(e.get("r") or 14) + pr + 4)
        if d > 0:
            depth += d
    for s in snap.get("shots") or []:
        dx, dy = float(s.get("dx") or 0), float(s.get("dy") or 0)
        vx, vy = float(s.get("vx") or 0), float(s.get("vy") or 0)
        rad = float(s.get("r") or 8) + pr + 8
        near = rad
        for t in (0.0, 0.18, 0.35):
            near = min(near, math.hypot(dx + vx * t - cx, dy + vy * t - cy))
        if near < rad:
            depth += rad - near
    return depth


def _walks_into(snap, label, pr):
    vx, vy = _VECS[label]
    length = math.hypot(vx, vy) or 1
    ux, uy = vx / length, vy / length
    return any(_touch(snap, ux * ahead, uy * ahead, pr) > 0 for ahead in _STEP)


def _danger(snap, cx, cy, pr):
    """Positive when this point sits in a trap blast or an enemy area."""
    depth = 0.0
    for h in snap.get("hazards") or []:
        if h.get("kind") == "beam":
            depth += _beam_depth(h, cx, cy, pr)
        else:
            d = _circle_depth(h.get("dx") or 0, h.get("dy") or 0, cx, cy, float(h.get("r") or 0) + pr * 0.6)
            if d > 0:
                depth += d
    for t in snap.get("traps") or []:
        d = _circle_depth(t.get("dx") or 0, t.get("dy") or 0, cx, cy, float(t.get("r") or 74) + pr * 0.4)
        if d > 0:
            depth += d
    return depth


# A rift closes in 45 seconds and winning it awards the move merge.
_GOAL_RANK = {"rift": 3, "thief": 2, "shiny": 1}


def _goal(snap):
    """An open rift first. While a boss is alive, a Meowth and a shiny wait."""
    boss = bool(snap.get("boss"))
    best = None
    best_key = None
    for g in snap.get("goals") or []:
        kind = g.get("kind")
        if kind not in _GOAL_RANK:
            continue
        if boss and kind != "rift":
            continue
        key = (_GOAL_RANK[kind], -float(g.get("dist") or 0))
        if best_key is None or key > best_key:
            best, best_key = g, key
    return best


def steer(snap):
    """Keep walking. Body contact and shots outrank goals, loot, and closing in.

    ponytail: eight fixed directions, rescored every snapshot. Short attacks use one
    16px probe toward the nearest foe. A real pathfinder would be the upgrade if it
    starts trapping itself in crowds.
    """
    safe = []
    for s in snap.get("safe") or []:
        if s in _VECS and s not in safe:
            safe.append(s)
    if not safe:
        return "stand still"
    pr = float(snap.get("r") or 14)
    nudge = snap.get("nudge")
    if nudge in safe and not _walks_into(snap, nudge, pr):
        return nudge
    goal = _goal(snap)
    if not goal:
        leave = _escape(snap, safe)
        if leave and not _walks_into(snap, leave, pr):
            return leave
    foes = snap.get("enemies") or []
    loot = snap.get("pickups") or []
    blocks = snap.get("blocks") or []
    heading = snap.get("heading") or ""
    tune = _tune(snap)
    dodge = float(tune.get("dodge") or 150)
    reach = _attack_reach(snap)
    style = tune.get("range") or ""
    if style == "long":
        reach = 0.0
    elif style == "short":
        reach = min(reach or dodge, dodge)
    gx = float(goal.get("dx") or 0) if goal else 0.0
    gy = float(goal.get("dy") or 0) if goal else 0.0
    best, best_score = safe[0], -1e18
    for label in safe:
        vx, vy = _VECS[label]
        length = math.hypot(vx, vy) or 1
        ux, uy = vx / length, vy / length
        score = 18 if label == heading else 0
        if reach:
            score += _close_score(ux, uy, foes, reach, pr)
        for ahead in _STEP:
            cx, cy = ux * ahead, uy * ahead
            depth = _danger(snap, cx, cy, pr)
            if depth > 0:
                score -= 1600 + depth * 8
            # Beats a full step toward a void, a shiny, or a short-range foe (~4300).
            touch = _touch(snap, cx, cy, pr)
            if touch > 0:
                score -= 8000 + touch * 8
            if goal:
                score += (math.hypot(gx, gy) - math.hypot(gx - cx, gy - cy)) * 40
            if not reach:
                for e in foes:
                    if e.get("shiny") or e.get("thief"):
                        continue
                    dist = math.hypot(float(e.get("dx") or 0) - cx, float(e.get("dy") or 0) - cy)
                    gap = float(e.get("r") or 14) + pr + 28
                    if dist < gap:
                        score -= 1200 + (gap - dist) * 10
                    elif dist < dodge:
                        score -= (dodge - dist) * (3 if e.get("boss") else 1.6)
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
NASTY = "nasty-plot"
HARDEN = "harden"


def _fuse_order(anchor):
    """The buff that matches the anchor, then Harden, then the other dance."""
    cat = anchor.get("cat") if anchor else ""
    dance = SWORDS if cat == "physical" else NASTY if cat == "special" else SWORDS
    other = NASTY if dance == SWORDS else SWORDS
    return (dance, HARDEN, other)


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


# Experience and damage first, then the other run-long stats. The berry is a one-shot heal.
_PERMANENT = ("xp", "atk", "hp", "rgn", "cd", "def", "mag", "spd")
_SURVIVE_ITEMS = ("focussash", "leftovers")
_ATK_ITEMS = ("choiceband", "lifeorb")


def _permanent(cards):
    by_stat = {}
    for i, card in enumerate(cards):
        sid = card.get("stat")
        if sid and sid not in by_stat:
            by_stat[sid] = i
    for sid in _PERMANENT:
        if sid in by_stat:
            return by_stat[sid]
    return None


def _in_use(moves):
    """Every move in the slot that is firing, attack and fused buffs together."""
    slots = {m.get("slot") for m in moves if m.get("active")}
    if slots:
        return [m for m in moves if m.get("slot") in slots]
    using = _using(moves)
    return [using] if using else []


def _done(m):
    """Top level, or already evolved. Further levels are not offered."""
    if m.get("evolved"):
        return True
    return (m.get("lvl") or 1) >= (m.get("max") or 5)


def _upgrade_of(cards, mid):
    return _find(cards, lambda c: c.get("move") == mid and (c.get("upgrade") or "→" in (c.get("label") or "")))


def _carry(moves, dodge):
    """Longest attack that still connects from the dodge distance. A dash is skipped."""
    far = [
        m for m in moves
        if m.get("kind") not in ("buff", "dash") and float(m.get("reach") or 0) >= float(dodge)
    ]
    if not far:
        return None
    return max(far, key=lambda m: (float(m.get("reach") or 0), m.get("lvl") or 0))


def level_choice(cards, moves, dodge=150, style=""):
    """Level the attack this run is keeping. A long run skips one that cannot hit from the dodge distance.
    A dash is skipped too.

    Once that attack is maxed or evolved, take a permanent stat. A move in another
    slot, and a move not yet learned, is left alone.
    """
    if style == "long":
        carry = _carry(moves, dodge)
        slot = carry.get("slot") if carry else None
        using = [m for m in moves if slot is not None and m.get("slot") == slot]
    else:
        using = _in_use(moves)
    attacks = [m for m in using if m.get("kind") not in ("buff", "dash")]
    buffs = [m for m in using if m.get("kind") == "buff"]
    for m in attacks + buffs:
        if _done(m):
            continue
        hit = _upgrade_of(cards, m.get("id"))
        if hit is not None:
            return hit
    hit = _permanent(cards)
    if hit is not None:
        return hit
    if attacks and all(_done(m) for m in attacks):
        for m in attacks:
            hit = _upgrade_of(cards, m.get("id"))
            if hit is not None:
                return hit
    return None


def bond_choice(cards, moves, bond, dodge=150, style=""):
    """Anchor the kept attack. Fuse the matching buff, then Harden, then a different-type attack."""
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
        calm = [im for im in opts if im[1].get("kind") != "dash"]
        if calm:
            opts = calm
        if not opts:
            return None
        if style == "long":
            far = [im for im in opts if float(im[1].get("reach") or 0) >= float(dodge)]
            if far:
                return max(far, key=lambda im: (float(im[1].get("reach") or 0), im[1].get("lvl") or 0))[0]
        return max(opts, key=lambda im: (im[1].get("lvl") or 0, im[1].get("name") or ""))[0]

    if mode == "pair" and step == 1:
        return strongest()
    if mode == "pair" and step == 2:
        first = by_id.get(bond.get("first"))
        for mid in _fuse_order(first):
            hit = named(mid)
            if hit is not None:
                return hit
        ftype = first.get("type") if first and first.get("kind") != "buff" else None
        if ftype:
            hit = strongest(lambda m: m.get("type") != ftype)
            if hit is not None:
                return hit
        return strongest()
    anchor = next((m for m in group if m.get("kind") != "buff"), None)
    for mid in _fuse_order(anchor):
        if mid not in have:
            hit = named(mid)
            if hit is not None:
                return hit
    buffs_done = {SWORDS, HARDEN} <= have or {NASTY, HARDEN} <= have
    if anchor and (len(group) >= 3 or buffs_done):
        hit = strongest(lambda m: m.get("type") != anchor.get("type"))
        if hit is not None:
            return hit
    return strongest()


def item_choice(cards, time, prefer=None):
    """The side-panel item first. Otherwise Focus Sash, then Leftovers, until 10 minutes.
    After that, Choice Band over Life Orb.
    """
    ids = [c.get("item") or "" for c in cards]
    if not any(ids):
        return None
    order = [prefer] if prefer else []
    order.extend(_SURVIVE_ITEMS if float(time or 0) < 600 else _ATK_ITEMS)
    seen = set()
    for iid in order:
        if not iid or iid in seen:
            continue
        seen.add(iid)
        for i, got in enumerate(ids):
            if got == iid:
                return i
    return None


def build_choice(snap):
    """Card index from the level-up, item, or bond rule. None leaves the card to Laya."""
    cards = snap.get("cards") or []
    if not cards:
        return None
    phase = snap.get("phase")
    tune = _tune(snap)
    dodge = float(tune.get("dodge") or 150)
    style = tune.get("range") or ""
    if phase == "levelup":
        return level_choice(cards, _moves(snap), dodge, style)
    if phase == "item":
        return item_choice(cards, snap.get("time") or 0, tune.get("item"))
    if phase == "bond":
        return bond_choice(cards, _moves(snap), snap.get("bond"), dodge, style)
    return None


def _note(source, question, result, scene, raw=None):
    note = {"source": source, "question": question, "scene": scene, "result": result or ""}
    if raw and raw != result:
        note["raw"] = raw
    return note


def decide(agent, snap):
    """Rule first. Laya only for a card the rule does not take.

    `agent` may be None while the phase is playing or a rule applies.
    The snapshot and the return value are described at the top of this file.
    """
    phase = snap.get("phase")
    scene = describe(snap) if phase in ("playing", "levelup", "item", "bond") else ""
    forced = build_choice(snap)
    if forced is not None:
        cards = snap.get("cards") or []
        label = cards[forced].get("label")
        return {
            "action": "card",
            "index": forced,
            "label": label,
            "shielded": False,
            "note": _note("rule", "Which option should the Pokémon take?", label, scene),
        }
    if phase not in ("levelup", "item", "bond"):
        if phase != "playing":
            return {"action": "wait"}
        label = steer(snap)
        return {
            "action": "move",
            "label": label,
            "shielded": False,
            "note": _note("rule", "Which way should the Pokémon move to stay alive?", label, scene),
        }
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
        "note": _note("laya", qs["pick"]["instructions"], label, scene, ans.get("choice")),
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
    noted = decide(None, {
        "phase": "playing", "hp": 40, "maxHp": 90, "level": 2, "time": 12,
        "enemies": [], "hazards": [], "pickups": [], "safe": ["move east"],
    })
    assert noted["action"] == "move" and noted["note"]["result"] == noted["label"], noted
    assert noted["note"]["source"] == "rule" and noted["note"]["question"].startswith("Which way"), noted
    assert "HP" in noted["note"]["scene"], noted
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
    assert level_choice(up[:1], moves) is None
    fused_buff = [dict(ember, slot=0, group=2), dict(swords, slot=0, group=2, active=False)]
    assert level_choice([
        {"label": "Danza Espada", "move": "swords-dance", "upgrade": True},
    ], fused_buff) == 0
    fourth = [
        {"label": "Lanzallamas", "move": "flamethrower", "kind": "cone", "type": "fire", "fresh": True},
        {"label": "Pistola Agua", "move": "water-gun", "kind": "projectile", "type": "water", "fresh": True},
    ]
    assert level_choice(fourth, [ember, swords, harden]) is None
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
    special = [
        {"label": "Danza Espada", "move": "swords-dance"},
        {"label": "Maquinación", "move": "nasty-plot"},
        {"label": "Fortaleza", "move": "harden"},
    ]
    hyper = {"id": "hyper-beam", "kind": "charge", "cat": "special", "type": "normal", "lvl": 1, "slot": 0, "active": True}
    assert bond_choice(special, [hyper], {"mode": "pair", "step": 2, "group": [], "first": "hyper-beam"}) == 1
    assert bond_choice(special, [dict(ember, cat="physical")], {"mode": "pair", "step": 2, "group": [], "first": "ember"}) == 0
    fused = [dict(ember, group=4)]
    assert level_choice([
        {"label": "Ascuas", "move": "ember", "upgrade": True},
        {"label": "Síntesis", "stat": "rgn"},
    ], fused) == 0
    assert level_choice([
        {"label": "Ascuas", "move": "ember", "upgrade": True},
        {"label": "Síntesis", "stat": "rgn"},
    ], [dict(ember, lvl=5, group=4)]) == 1
    assert level_choice([
        {"label": "Ascuas → Llamarada", "move": "ember", "upgrade": True},
        {"label": "Aprendizaje", "stat": "xp"},
    ], [dict(ember, evolved=True, group=4)]) == 1
    assert level_choice([
        {"label": "Pistola Agua", "move": "water-gun", "upgrade": True},
        {"label": "Vigor", "stat": "hp"},
    ], moves) == 1
    assert level_choice([
        {"label": "Potencia", "stat": "atk"},
        {"label": "Aprendizaje", "stat": "xp"},
    ], fused) == 1
    assert level_choice([
        {"label": "Síntesis", "stat": "rgn"},
        {"label": "Potencia", "stat": "atk"},
    ], [dict(ember, lvl=5)]) == 1
    short = dict(ember, reach=48)
    far_move = dict(water, reach=400, slot=1, active=False)
    assert level_choice([
        {"label": "Ascuas", "move": "ember", "upgrade": True},
        {"label": "Aprendizaje", "stat": "xp"},
    ], [short], 150, "long") == 1
    assert level_choice([
        {"label": "Ascuas", "move": "ember", "upgrade": True},
        {"label": "Pistola Agua", "move": "water-gun", "upgrade": True},
    ], [short, far_move], 150, "long") == 1
    dash = {"id": "quick-attack", "kind": "dash", "reach": 170, "slot": 0, "active": True, "lvl": 1}
    beam = {"id": "water-gun", "kind": "projectile", "reach": 200, "slot": 1, "active": False, "lvl": 1}
    assert level_choice([
        {"label": "Ataque Rápido", "move": "quick-attack", "upgrade": True},
        {"label": "Pistola Agua", "move": "water-gun", "upgrade": True},
    ], [dash, beam], 150, "long") == 1
    assert level_choice([
        {"label": "Ataque Rápido", "move": "quick-attack", "upgrade": True},
        {"label": "Aprendizaje", "stat": "xp"},
    ], [dash], 150, "long") == 1
    assert bond_choice([
        {"label": "Ataque Rápido", "move": "quick-attack", "kind": "dash", "fresh": True},
        {"label": "Pistola Agua", "move": "water-gun", "kind": "projectile", "type": "water"},
    ], [dash, beam], {"mode": "pair", "step": 1, "group": [], "first": ""}, 150, "long") == 1
    assert level_choice([
        {"label": "Lanzallamas", "move": "flamethrower", "fresh": True, "kind": "cone"},
        {"label": "Ascuas", "move": "ember", "upgrade": True},
    ], fused) == 1
    assert item_choice([
        {"item": "leftovers"}, {"item": "focussash"},
    ], 120) == 1
    assert item_choice([
        {"item": "luckyegg"}, {"item": "choiceband"},
    ], 120) is None
    assert item_choice([
        {"item": "luckyegg"}, {"item": "choiceband"},
    ], 600) == 1
    assert item_choice([
        {"item": "choiceband"}, {"item": "lifeorb"},
    ], 700) == 0
    assert item_choice([{"item": "leftovers"}, {"item": "shellbell"}], 100) == 0
    assert item_choice([
        {"item": "luckyegg"}, {"item": "lifeorb"},
    ], 100, "lifeorb") == 1
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
    melee = [{"id": "tackle", "kind": "melee", "lvl": 1, "active": True, "reach": 48}]
    assert steer({
        "safe": ways, "r": 14, "moves": melee, "pickups": [],
        "enemies": [{"dx": 0, "dy": -100, "r": 16}],
    }) == "move north"
    assert steer({
        "safe": ways, "r": 14, "moves": melee,
        "enemies": [{"dx": 0, "dy": -100, "r": 16}],
        "pickups": [{"kind": "coin", "dx": 0, "dy": 80}],
    }) == "move north"
    assert steer({
        "safe": ways, "r": 14, "moves": melee, "pickups": [],
        "enemies": [{"dx": 0, "dy": -20, "r": 16}],
    }) != "move north"
    far = [{"id": "ember", "kind": "projectile", "lvl": 1, "active": True, "reach": 496}]
    assert steer({
        "safe": ways, "r": 14, "moves": far, "pickups": [],
        "enemies": [{"dx": 0, "dy": -50, "r": 16}],
    }) == "move south"

    def trail(pts, secs=5):
        n = len(pts)
        return [
            {"x": x, "y": y, "dt": 0 if n == 1 else secs * (n - 1 - i) / (n - 1)}
            for i, (x, y) in enumerate(pts)
        ]

    assert steer({
        "safe": ways, "r": 14, "x": 100, "y": 100, "heading": "move east",
        "trail": trail([(100, 100), (112, 96), (90, 108), (104, 88), (100, 100)]),
        "enemies": [{"dx": 0, "dy": -40, "r": 16}],
        "pickups": [{"kind": "coin", "dx": 80, "dy": 0}],
    }) == "move west"
    assert steer({
        "safe": ways, "r": 14, "x": 100, "y": 0, "heading": "move south",
        "trail": trail([(100, 30), (108, 40), (92, 48), (100, 36), (104, 44)]),
        "enemies": [], "pickups": [],
    }) == "move north"
    assert steer({
        "safe": ways, "r": 14, "x": 200, "y": 0, "heading": "move east",
        "trail": trail([(i * 40, 0) for i in range(6)]),
        "enemies": [], "pickups": [],
    }) == "move east"
    assert steer({
        "safe": ways, "r": 14, "x": 100, "y": 100, "heading": "move east",
        "trail": trail([(100, 100), (104, 100), (100, 102)], 2),
        "enemies": [], "pickups": [],
    }) == "move east"
    assert steer({
        "safe": ways, "r": 14, "enemies": [],
        "pickups": [{"kind": "coin", "dx": 80, "dy": 0}],
        "traps": [{"kind": "boom", "dx": 40, "dy": 0, "r": 74}],
    }) == "move west"
    assert steer({
        "safe": ways, "r": 14, "enemies": [], "pickups": [],
        "hazards": [{"kind": "zone", "dx": 0, "dy": -30, "r": 50, "delay": 1.2}],
    }) == "move south"
    assert steer({
        "safe": ways, "r": 14,
        "enemies": [{"dx": 80, "dy": 0, "r": 16, "shiny": True}],
        "goals": [{"kind": "shiny", "dx": 80, "dy": 0, "dist": 80}],
        "pickups": [{"kind": "coin", "dx": -100, "dy": 0}],
    }) == "move east"
    assert steer({
        "safe": ways, "r": 14, "nudge": "move north",
        "enemies": [{"dx": 80, "dy": 0, "r": 16, "shiny": True}],
        "goals": [{"kind": "shiny", "dx": 80, "dy": 0, "dist": 80}],
        "pickups": [{"kind": "coin", "dx": 80, "dy": 0}],
    }) == "move north"
    assert steer({
        "safe": ["move east"], "r": 14, "nudge": "move north",
        "enemies": [], "pickups": [], "heading": "move east",
    }) == "move east"
    assert steer({
        "safe": ways, "r": 14, "enemies": [], "pickups": [],
        "goals": [
            {"kind": "rift", "dx": 40, "dy": 0, "dist": 40},
            {"kind": "thief", "dx": 0, "dy": -120, "dist": 120},
        ],
    }) == "move east"
    assert steer({
        "safe": ways, "r": 14, "boss": True, "enemies": [],
        "goals": [{"kind": "thief", "dx": 80, "dy": 0, "dist": 80}],
        "pickups": [{"kind": "coin", "dx": -100, "dy": 0}],
    }) == "move west"
    assert steer({
        "safe": ways, "r": 14, "boss": True, "enemies": [], "pickups": [],
        "goals": [
            {"kind": "rift", "dx": 80, "dy": 0, "dist": 80},
            {"kind": "thief", "dx": 0, "dy": -120, "dist": 120},
        ],
    }) == "move east"
    blocked = steer({
        "safe": ways, "r": 14, "pickups": [],
        "enemies": [{"dx": 24, "dy": 0, "r": 16}],
        "goals": [{"kind": "rift", "dx": 80, "dy": 0, "dist": 80}],
    })
    assert blocked != "move east", blocked
    sidestep = steer({
        "safe": ways, "r": 14, "enemies": [], "pickups": [],
        "shots": [{"dx": 0, "dy": -30, "vx": 0, "vy": 190, "r": 7}],
    })
    assert sidestep in ("move east", "move west"), sidestep
    assert steer({
        "safe": ways, "r": 14, "nudge": "move north", "enemies": [], "pickups": [],
        "shots": [{"dx": 0, "dy": -16, "vx": 0, "vy": 190, "r": 8}],
    }) != "move north"
    assert steer({
        "safe": ways, "r": 14, "pickups": [],
        "enemies": [{"dx": 0, "dy": -100, "r": 16}],
        "tune": {"dodge": 40, "range": "long"},
    }) == "move north"
    assert steer({
        "safe": ways, "r": 14, "moves": melee, "pickups": [],
        "enemies": [{"dx": 0, "dy": -100, "r": 16}],
        "tune": {"range": "long", "dodge": 150},
    }) == "move south"
    assert steer({
        "safe": ways, "r": 14, "moves": far, "pickups": [],
        "enemies": [{"dx": 0, "dy": -100, "r": 16}],
        "tune": {"range": "short", "dodge": 80},
    }) == "move north"
    print("ok")


if __name__ == "__main__":
    _check()
