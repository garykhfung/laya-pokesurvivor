/* Drives a local solo run. Injected only by laya-mlx/play.py into dev.html. */
(function () {
  var STEP = 40;
  var HAZARD_SOON = 0.7;
  var DIRS = [
    ['stand still', 0, 0],
    ['move north', 0, -1],
    ['move north-east', 1, -1],
    ['move east', 1, 0],
    ['move south-east', 1, 1],
    ['move south', 0, 1],
    ['move south-west', -1, 1],
    ['move west', -1, 0],
    ['move north-west', -1, -1]
  ];
  var MOVE_KEYS = {
    'stand still': [],
    'move north': ['KeyW'],
    'move south': ['KeyS'],
    'move west': ['KeyA'],
    'move east': ['KeyD'],
    'move north-east': ['KeyW', 'KeyD'],
    'move south-east': ['KeyS', 'KeyD'],
    'move south-west': ['KeyS', 'KeyA'],
    'move north-west': ['KeyW', 'KeyA']
  };

  var held = {};
  var busy = false;
  var loop = false;
  var cardSent = '';
  var overHandled = false;
  var mark = 0;
  var trail = [];
  var spot = null;
  var nudge = '';

  function key(type, code) {
    window.dispatchEvent(new KeyboardEvent(type, {
      code: code, key: code, bubbles: true, cancelable: true
    }));
  }

  function tap(code) {
    key('keydown', code);
    key('keyup', code);
  }

  function releaseMove() {
    Object.keys(held).forEach(function (code) { key('keyup', code); });
    held = {};
  }

  function holdMove(label) {
    var want = MOVE_KEYS[label] || [];
    Object.keys(held).forEach(function (code) {
      if (want.indexOf(code) < 0) { key('keyup', code); delete held[code]; }
    });
    want.forEach(function (code) {
      if (!held[code]) { key('keydown', code); held[code] = true; }
    });
  }

  function open(id) {
    return G.UI && G.UI.isOpen(id);
  }

  function phase() {
    if (!G.Game) return 'other';
    if (G.Judgment && G.Judgment.cinematic && G.Game.state === 'playing') return 'cinematic';
    if (G.Game.state === 'over' || open('scr-over')) return 'over';
    if (G.Game.state === 'levelup' || open('scr-levelup')) return 'levelup';
    if (open('scr-item')) return 'item';
    if (open('scr-bond')) return 'bond';
    if (G.Game.state === 'playing') return 'playing';
    return 'other';
  }

  function cardBox(ph) {
    return document.getElementById(ph === 'levelup' ? 'cards' : ph === 'item' ? 'item-cards' : 'bond-cards');
  }

  function cardsOf(ph) {
    var box = cardBox(ph);
    if (!box) return [];
    var used = {};
    return [].map.call(box.querySelectorAll('.card'), function (el) {
      var nm = el.querySelector('.nm');
      var chip = nm && nm.querySelector('.tag-chip');
      var title = '';
      if (nm) {
        [].forEach.call(nm.childNodes, function (n) {
          if (n !== chip && n.nodeType === 3) title += n.textContent;
        });
        title = title.replace(/\s+/g, ' ').trim() || nm.textContent.replace(/\s+/g, ' ').trim();
      }
      var tag = chip ? chip.textContent.replace(/\s+/g, ' ').trim() : '';
      var ds = ((el.querySelector('.ds') || {}).textContent || '').replace(/\s+/g, ' ').trim();
      var label = title || 'reward';
      if (used[label]) label = (title + ' ' + (tag || Object.keys(used).length)).trim();
      used[label] = true;
      var raw = title.split('→')[0].split('->')[0].trim();
      var def = (G.Moves && G.Moves.ALL || []).find(function (m) {
        return m.name === raw || (G.T && G.T(m.name) === raw);
      });
      var stat = '';
      ((G.LevelUp && G.LevelUp.STATS) || []).some(function (s) {
        if (s.title === raw || (G.T && G.T(s.title) === raw)) { stat = s.id; return true; }
        return false;
      });
      var item = '';
      ((G.Items && G.Items.LIST) || []).some(function (it) {
        var en = G.T ? G.T(it.name) : '';
        if (title.indexOf(it.name) >= 0 || (en && title.indexOf(en) >= 0)) { item = it.id; return true; }
        return false;
      });
      return {
        label: label,
        detail: [tag, ds].filter(Boolean).join('. ').slice(0, 180),
        move: def ? def.id : '',
        type: def ? def.type : '',
        kind: def ? def.kind : '',
        stat: stat,
        item: item,
        upgrade: /^(Nv\.|Lv\.)/.test(tag) || title.indexOf('→') >= 0 || title.indexOf('->') >= 0,
        fresh: tag.indexOf('Nuevo') === 0 || tag.indexOf('New') === 0
      };
    });
  }

  /** Distance at which this move actually connects. 0 for buffs and ground traps. */
  function hitReach(m) {
    if (!m || m.kind === 'buff' || m.kind === 'trail' || m.kind === 'mine' || m.kind === 'turret') return 0;
    if (m.kind === 'cone' || m.kind === 'nova' || m.kind === 'projectile') {
      return Math.round((m.speed || 0) * (m.life || 0));
    }
    if (m.kind === 'chain') return 280;
    if (m.kind === 'meteor') return 340;
    return Math.round(m.radius || 0);
  }

  function knownMoves(pl) {
    var out = [];
    (pl.slots || []).forEach(function (g, si) {
      g.forEach(function (m, mi) {
        out.push({
          name: m.name, id: m.id, kind: m.kind, type: m.type, lvl: m.lvl,
          max: m.maxLvl || (m.def && m.def.maxLvl) || 5, evolved: !!m.evolved,
          slot: si, lead: mi === 0, active: si === pl.active, group: g.length,
          reach: hitReach(m)
        });
      });
    });
    return out;
  }

  function bondInfo(pl) {
    if (!open('scr-bond') || !pl) return null;
    var sub = (document.getElementById('bond-sub') || {}).textContent || '';
    var grow = null;
    pl.slots.forEach(function (g) { if (g.length > 1 && g.length < 4) grow = g; });
    var back = document.getElementById('bond-back');
    var step = back && !back.classList.contains('hidden') ? 2 : 1;
    var first = '', bestLen = 0;
    if (step === 2) {
      pl.moves.forEach(function (m) {
        [m.name, G.T ? G.T(m.name) : ''].forEach(function (n) {
          if (n && sub.indexOf(n) >= 0 && n.length > bestLen) { bestLen = n.length; first = m.id; }
        });
      });
    }
    return {
      mode: grow ? 'add' : 'pair',
      step: step,
      group: grow ? grow.map(function (m) { return m.id; }) : [],
      first: first
    };
  }

  function hasAttack(g) {
    return (g || []).some(function (m) { return m.kind !== 'buff'; });
  }

  /** The short or long attack, whichever this run asked for. A buff-only slot is never selected. */
  function useBestAttack() {
    var pl = G.Game.player;
    if (!pl || !pl.slots || !pl.slots.length) return;
    var wantShort = tune.range === 'short';
    var bestSlot = -1, bestKey = 0, bestLvl = -1;
    pl.slots.forEach(function (g, i) {
      if (!hasAttack(g)) return;
      var reach = 0, lvl = 0;
      g.forEach(function (m) {
        if (m.kind === 'buff') return;
        reach = Math.max(reach, hitReach(m));
        if (m.lvl > lvl) lvl = m.lvl;
      });
      var key = wantShort ? (reach || 9999) : -reach;
      if (bestSlot < 0 || key < bestKey || (key === bestKey && lvl > bestLvl)) {
        bestKey = key; bestLvl = lvl; bestSlot = i;
      }
    });
    if (bestSlot >= 0 && bestSlot !== pl.active) tap('Digit' + (bestSlot + 1));
  }

  function heading() {
    var w = !!held.KeyW, a = !!held.KeyA, s = !!held.KeyS, d = !!held.KeyD;
    if (w && d && !a && !s) return 'move north-east';
    if (s && d && !w && !a) return 'move south-east';
    if (s && a && !w && !d) return 'move south-west';
    if (w && a && !s && !d) return 'move north-west';
    if (w && !a && !s && !d) return 'move north';
    if (d && !w && !a && !s) return 'move east';
    if (s && !w && !a && !d) return 'move south';
    if (a && !w && !s && !d) return 'move west';
    return '';
  }

  var TRAP_R = 74;

  /** How deep (x, y) sits in an enemy area or a poison, sticky, or blast trap. */
  function dangerDepth(x, y, pl) {
    var depth = 0;
    (G.Hazards.all || []).forEach(function (h) {
      if (h.hit || !G.Hazards.inside(h, x, y, pl.r)) return;
      if (h.kind === 'zone') depth += (h.r + pl.r * 0.6) - Math.hypot(x - h.x, y - h.y);
      else depth += 24;
    });
    if (G.World.specialsNear) {
      G.World.specialsNear(x, y, TRAP_R + 40).forEach(function (p) {
        if (p.type !== 'trap') return;
        var dist = Math.hypot(x - p.x, y - (p.y - 8));
        var rad = TRAP_R + pl.r * 0.4;
        if (dist < rad) depth += rad - dist;
      });
    }
    return depth;
  }

  function safeDirs(pl) {
    var here = dangerDepth(pl.x, pl.y, pl);
    var out = [];
    DIRS.forEach(function (d) {
      var len = Math.hypot(d[1], d[2]) || 1;
      var x = pl.x + (d[0] === 'stand still' ? 0 : d[1] / len * STEP);
      var y = pl.y + (d[0] === 'stand still' ? 0 : d[2] / len * STEP);
      // isFree also rejects water. The shield will not step into a pond.
      if (d[0] !== 'stand still' && !G.World.isFree(x, y, pl.r)) return;
      // Deeper into an area is refused. A step out of one we already occupy stays open.
      if (dangerDepth(x, y, pl) > here + 1) return;
      out.push(d[0]);
    });
    return out;
  }

  /** Walls and indestructible props. Shots die on them, so the bot stays clear. */
  function blocksNear(pl) {
    var TS = G.World.TS || 32;
    var reach = 120;
    var seen = {};
    var out = [];
    var x0 = pl.x - reach, x1 = pl.x + reach, y0 = pl.y - reach, y1 = pl.y + reach;
    for (var y = y0; y <= y1; y += TS) {
      for (var x = x0; x <= x1; x += TS) {
        var block = G.World.projectileBlock(x, y, 4);
        if (!block) continue;
        if (block === 'wall') {
          var tx = Math.floor(x / TS), ty = Math.floor(y / TS);
          var key = 'w' + tx + ',' + ty;
          if (seen[key]) continue;
          seen[key] = true;
          var cx = tx * TS + TS / 2, cy = ty * TS + TS / 2;
          var dx = cx - pl.x, dy = cy - pl.y;
          out.push({ dx: Math.round(dx), dy: Math.round(dy), r: 16, dist: Math.round(Math.hypot(dx, dy)) });
        } else if (block.solid && !block.destructible) {
          var pkey = 'p' + block.tx + ',' + block.ty;
          if (seen[pkey]) continue;
          seen[pkey] = true;
          var pdx = block.x - pl.x, pdy = block.y - pl.y;
          out.push({ dx: Math.round(pdx), dy: Math.round(pdy), r: Math.round(block.r || 13), dist: Math.round(Math.hypot(pdx, pdy)) });
        }
      }
    }
    out.sort(function (a, b) { return a.dist - b.dist; });
    return out.slice(0, 20);
  }

  /** Last 5s of feet, sampled while actually playing. */
  function trackPos() {
    var pl = G.Game.player;
    if (!pl) return;
    var now = Date.now();
    var last = trail[trail.length - 1];
    if (last && now - last.t < 200) return;
    trail.push({ t: now, x: pl.x, y: pl.y });
    var cut = now - 5500;
    while (trail.length && trail[0].t < cut) trail.shift();
  }

  /** Every 3s, compare feet to the last sample. A nearby sample holds a random safe direction until the next one. */
  function wander() {
    var pl = G.Game.player;
    if (!pl) { spot = null; nudge = ''; return; }
    var now = Date.now();
    if (!spot) { spot = { x: pl.x, y: pl.y, t: now }; nudge = ''; return; }
    if (now - spot.t < 3000) return;
    var dist = Math.hypot(pl.x - spot.x, pl.y - spot.y);
    spot = { x: pl.x, y: pl.y, t: now };
    if (dist > 120) { nudge = ''; return; }
    var dirs = safeDirs(pl).filter(function (s) { return s !== 'stand still' && s !== heading(); });
    if (!dirs.length) dirs = safeDirs(pl).filter(function (s) { return s !== 'stand still'; });
    nudge = dirs.length ? dirs[(Math.random() * dirs.length) | 0] : '';
  }

  function posTrail() {
    var now = Date.now();
    return trail.filter(function (p) { return now - p.t <= 5000; }).map(function (p) {
      return { x: Math.round(p.x), y: Math.round(p.y), dt: Math.round((now - p.t) / 100) / 10 };
    });
  }

  function snapshot(ph) {
    var pl = G.Game.player;
    var foes = [];
    if (pl && G.EnemyMgr) {
      foes = G.EnemyMgr.all().filter(function (e) { return e && !e.dead; }).map(function (e) {
        var dx = e.x - pl.x, dy = e.y - pl.y;
        return {
          name: e.name, boss: !!e.boss, shiny: !!e.shiny, thief: e.behavior === 'thief',
          r: Math.round(e.r || 14), dist: Math.round(Math.hypot(dx, dy)), dx: Math.round(dx), dy: Math.round(dy)
        };
      }).filter(function (e) { return e.dist < 260; }).sort(function (a, b) { return a.dist - b.dist; }).slice(0, 12);
    }
    var pickups = [];
    if (pl && G.Pickups) {
      pickups = G.Pickups.all().filter(function (p) {
        return p && (p.kind === 'xp' || p.kind === 'xpBig' || p.kind === 'coin');
      }).map(function (p) {
        var dx = p.x - pl.x, dy = p.y - pl.y;
        return { kind: p.kind, dist: Math.round(Math.hypot(dx, dy)), dx: Math.round(dx), dy: Math.round(dy) };
      }).filter(function (p) { return p.dist < 320; }).sort(function (a, b) { return a.dist - b.dist; }).slice(0, 16);
    }
    var hazards = [];
    if (pl && G.Hazards) {
      hazards = (G.Hazards.all || []).filter(function (h) {
        if (h.hit) return false;
        var dist = Math.hypot((h.x || 0) - pl.x, (h.y || 0) - pl.y);
        return dist < 420 || (h.delay - h.t) < HAZARD_SOON || G.Hazards.inside(h, pl.x, pl.y, pl.r);
      }).slice(0, 8).map(function (h) {
        var dx = h.x - pl.x, dy = h.y - pl.y;
        return {
          kind: h.kind, dist: Math.round(Math.hypot(dx, dy)), dx: Math.round(dx), dy: Math.round(dy),
          r: Math.round(h.r || 0), angle: h.angle || 0, len: Math.round(h.len || 0), w: Math.round(h.w || 0),
          delay: Math.round((h.delay - h.t) * 10) / 10
        };
      });
    }
    var traps = [];
    if (pl && G.World.specialsNear) {
      traps = G.World.specialsNear(pl.x, pl.y, 200).filter(function (p) { return p.type === 'trap'; }).map(function (p) {
        var dx = p.x - pl.x, dy = (p.y - 8) - pl.y;
        return { kind: ['poison', 'sticky', 'boom'][p.kind] || 'trap', dx: Math.round(dx), dy: Math.round(dy), r: TRAP_R };
      });
    }
    var shots = [];
    if (pl && G.Projectiles && G.Projectiles.all) {
      shots = G.Projectiles.all().filter(function (p) {
        return p && !p.friendly && !p.orb;
      }).map(function (p) {
        var dx = p.x - pl.x, dy = p.y - pl.y;
        return {
          dx: Math.round(dx), dy: Math.round(dy),
          vx: Math.round(p.vx || 0), vy: Math.round(p.vy || 0),
          r: Math.round(p.r || 8), dist: Math.round(Math.hypot(dx, dy))
        };
      }).filter(function (p) { return p.dist < 340; }).sort(function (a, b) { return a.dist - b.dist; }).slice(0, 12);
    }
    var goals = [];
    if (pl && G.Rift && G.Rift.rift && !G.Rift.arena) {
      var rdx = G.Rift.rift.x - pl.x, rdy = G.Rift.rift.y - pl.y;
      goals.push({ kind: 'rift', dx: Math.round(rdx), dy: Math.round(rdy), dist: Math.round(Math.hypot(rdx, rdy)) });
    }
    if (pl && G.EnemyMgr) {
      G.EnemyMgr.all().forEach(function (e) {
        if (!e || e.dead || (!e.shiny && e.behavior !== 'thief')) return;
        var dx = e.x - pl.x, dy = e.y - pl.y, dist = Math.hypot(dx, dy);
        if (dist > 800) return;
        goals.push({
          kind: e.behavior === 'thief' ? 'thief' : 'shiny',
          dx: Math.round(dx), dy: Math.round(dy), dist: Math.round(dist)
        });
      });
    }
    var liquid = null;
    if (pl && pl.inLiquid && G.World.liquidEffect(pl.mon.types).dmg) liquid = G.World.liquidKind();
    return {
      phase: ph,
      hp: pl ? Math.round(pl.hp) : 0,
      maxHp: pl ? Math.round(pl.maxHp) : 1,
      x: pl ? Math.round(pl.x) : 0,
      y: pl ? Math.round(pl.y) : 0,
      r: pl ? pl.r : 14,
      heading: heading(),
      level: pl ? pl.level : 1,
      mon: pl && pl.mon ? pl.mon.name : '',
      time: G.Game.st ? G.Game.st.time : 0,
      liquid: liquid,
      enemies: foes,
      pickups: pickups,
      blocks: pl ? blocksNear(pl) : [],
      traps: traps,
      hazards: hazards,
      shots: shots,
      goals: goals,
      safe: pl && ph === 'playing' ? safeDirs(pl) : [],
      nudge: ph === 'playing' ? nudge : '',
      trail: ph === 'playing' ? posTrail() : [],
      cards: ph === 'playing' || ph === 'cinematic' || ph === 'over' ? [] : cardsOf(ph),
      moves: pl ? knownMoves(pl) : [],
      bond: ph === 'bond' ? bondInfo(pl) : null,
      tune: { dodge: tune.dodge, range: tune.range, item: tune.item }
    };
  }

  var logKey = '';
  var tune = { dodge: 150, range: 'long', item: 'luckyegg', randomMon: false };
  var runSetup = { dex: 0, shiny: false, levels: {} };
  try {
    var kept = JSON.parse(localStorage.getItem('laya.tune') || 'null');
    if (kept && typeof kept === 'object') {
      if (kept.dodge) tune.dodge = Math.max(40, Math.min(400, kept.dodge | 0));
      if (kept.range === 'short' || kept.range === 'long') tune.range = kept.range;
      if (kept.item) tune.item = kept.item;
      if (kept.randomMon) tune.randomMon = true;
    }
  } catch (e) { /* keep the defaults */ }

  function saveTune() {
    try { localStorage.setItem('laya.tune', JSON.stringify(tune)); } catch (e) { /* private mode */ }
  }

  function mountTune(panel) {
    var box = panel.querySelector('.tune');
    if (!box || box.childNodes.length) return;
    var field = 'display:flex;justify-content:space-between;align-items:center;gap:8px;margin:4px 0';
    var control = 'width:132px;background:#12203a;color:#e8f0ff;border:1px solid #6ea8ff;border-radius:6px;padding:2px 6px';
    box.innerHTML = '<div style="color:#9ec0ff;font-size:11px">Each run</div>' +
      '<label style="' + field + '">Dodge distance<input class="dodge" type="number" min="40" max="400" step="10" style="' + control + '"></label>' +
      '<label style="' + field + '">Attack range<select class="range" style="' + control + '">' +
      '<option value="short">Short</option><option value="long">Long</option></select></label>' +
      '<label style="' + field + '">Item priority<select class="item" style="' + control + '"></select></label>' +
      '<button type="button" class="rand" style="width:100%;margin-top:4px;background:#12203a;color:#e8f0ff;border:1px solid #6ea8ff;border-radius:6px;padding:4px 6px"></button>';
    var item = box.querySelector('.item');
    ((G.Items && G.Items.LIST) || []).forEach(function (it) {
      var opt = document.createElement('option');
      opt.value = it.id;
      opt.textContent = (G.T && G.T(it.name)) || it.name;
      item.appendChild(opt);
    });
    box.querySelector('.dodge').value = tune.dodge;
    box.querySelector('.range').value = tune.range;
    if (item.querySelector('option[value="' + tune.item + '"]')) item.value = tune.item;
    box.addEventListener('change', function (ev) {
      var t = ev.target;
      if (t.classList.contains('dodge')) tune.dodge = Math.max(40, Math.min(400, t.value | 0 || 150));
      else if (t.classList.contains('range')) tune.range = t.value === 'short' ? 'short' : 'long';
      else if (t.classList.contains('item')) tune.item = t.value;
      saveTune();
    });
    var rand = box.querySelector('.rand');
    function showRand() { rand.textContent = tune.randomMon ? 'Random Pokémon: on' : 'Random Pokémon'; }
    showRand();
    rand.onclick = function () {
      tune.randomMon = !tune.randomMon;
      saveTune();
      showRand();
    };
  }

  function ensurePanel() {
    var panel = document.getElementById('bot-log');
    if (panel) return panel;
    panel = document.createElement('aside');
    panel.id = 'bot-log';
    panel.setAttribute('style', [
      'position:fixed', 'top:12px', 'right:12px', 'z-index:50',
      'width:min(320px,34vw)', 'max-height:calc(100dvh - 24px)', 'overflow:auto',
      'padding:12px 14px', 'border-radius:12px', 'pointer-events:auto',
      'background:rgba(8,16,36,.92)', 'border:2px solid #6ea8ff',
      'color:#e8f0ff', 'font:13px/1.35 sans-serif', 'text-shadow:none'
    ].join(';'));
    panel.innerHTML = '<b style="font-size:15px">Laya</b>' +
      '<div class="tune" style="margin-top:8px;color:#c5d4ee;font-size:12px"></div>' +
      '<div class="v" style="margin-top:8px;color:#c5d4ee;font-size:12px;line-height:1.45"></div>' +
      '<div style="margin-top:8px;color:#9ec0ff;font-size:11px">Question</div>' +
      '<p class="q" style="margin:2px 0 4px"></p>' +
      '<p class="s" style="margin:0 0 8px;color:#c5d4ee"></p>' +
      '<div style="color:#9ec0ff;font-size:11px">Response</div>' +
      '<p class="a" style="margin:2px 0 6px;font-weight:700"></p>' +
      '<div style="color:#9ec0ff;font-size:11px">Response time</div>' +
      '<p class="t" style="margin:2px 0 8px;font-weight:700"></p>' +
      '<div class="h"></div>';
    document.body.appendChild(panel);
    mountTune(panel);
    return panel;
  }

  function pct(v) { return Math.round(v * 100) + '%'; }

  function clock(s) {
    s = Math.max(0, Math.round(s || 0));
    return Math.floor(s / 60) + ':' + (s % 60 < 10 ? '0' : '') + (s % 60);
  }

  var records = {};
  try { records = JSON.parse(localStorage.getItem('laya.records') || '{}') || {}; } catch (e) { records = {}; }

  function saveRecords() {
    try { localStorage.setItem('laya.records', JSON.stringify(records)); } catch (e) { /* private mode */ }
  }

  function pullRecords() {
    fetch('/records').then(function (r) { return r.ok ? r.json() : null; }).then(function (book) {
      if (!book || typeof book !== 'object') return;
      records = book;
      saveRecords();
      paintVars();
    }).catch(function () {});
  }

  function keepLocal(report) {
    var key = String(report.dex);
    var row = records[key] || { name: report.name, dex: report.dex, played: 0, best: 0, bestRun: null };
    row.name = report.name;
    row.played = (row.played || 0) + 1;
    if ((report.time || 0) >= (row.best || 0)) {
      row.best = report.time || 0;
      row.bestRun = report;
    }
    records[key] = row;
    saveRecords();
  }

  function titled(text) { return (G.T && G.T(text)) || text; }

  function maxLevels() {
    var levels = {};
    ((G.Upgrades && G.Upgrades.LIST) || []).forEach(function (u) {
      levels[u.id] = u.max;
    });
    return levels;
  }

  function upgradeLine() {
    return ((G.Upgrades && G.Upgrades.LIST) || []).filter(function (u) {
      return runSetup.levels && runSetup.levels[u.id];
    }).map(function (u) { return titled(u.name) + ' ' + runSetup.levels[u.id]; });
  }

  function rememberRun() {
    var pl = G.Game && G.Game.player;
    if (!pl || !pl.mon) return;
    var statNames = (G.LevelUp && G.LevelUp.STATS) || [];
    var report = {
      dex: pl.dex,
      name: pl.mon.name,
      shiny: !!pl.shiny,
      time: Math.round((G.Game.st && G.Game.st.time) || 0),
      level: pl.level,
      kills: pl.kills || 0,
      bosses: pl.bosses || 0,
      item: (pl.item && G.Items && G.Items.BY[pl.item]) ? titled(G.Items.BY[pl.item].name) : '',
      atk: Math.round(pl.atkFor({ cat: 'physical' }) * 100) / 100,
      spa: Math.round(pl.atkFor({ cat: 'special' }) * 100) / 100,
      spd: Math.round(pl.spd),
      hp: Math.round(pl.maxHp),
      stats: Object.keys(pl.statsTaken || {}).map(function (id) {
        var s = statNames.filter(function (x) { return x.id === id; })[0];
        return titled(s ? s.title : id) + ' ×' + pl.statsTaken[id];
      }),
      moves: (pl.moves || []).map(function (m) {
        return (m.name || m.id) + ' ' + m.lvl + (m.evolved ? ' evolved' : '');
      }),
      upgrades: upgradeLine()
    };
    fetch('/record', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(report)
    }).then(function (r) {
      if (!r.ok) throw new Error('record ' + r.status);
      return r.json();
    }).then(function (book) {
      if (book && typeof book === 'object') { records = book; saveRecords(); paintVars(); }
    }).catch(function () { keepLocal(report); paintVars(); });
  }

  // Live numbers for this run. The knobs above the list are what changes between runs.
  function paintVars() {
    var panel = document.getElementById('bot-log');
    var pl = window.G && G.Game && G.Game.player;
    if (!pl) {
      if (panel) panel.querySelector('.v').textContent = '';
      return;
    }
    panel = ensurePanel();
    mountTune(panel);
    var t = (G.Game.st && G.Game.st.time) || 0;
    var atk = pl.atkFor({ cat: 'physical' });
    var spa = pl.atkFor({ cat: 'special' });
    var cd = pl.cdMul < 0.995 ? '−' + pct(1 - pl.cdMul) : '0%';
    var it = pl.item && G.Items && G.Items.BY[pl.item];
    var lines = [
      'This run',
      'HP ' + Math.round(pl.hp) + '/' + Math.round(pl.maxHp) + '  Lv ' + pl.level + '  ' + Math.round(t) + 's',
      pl.kills + ' KOs  ' + pl.bosses + ' bosses  ' + pl.coins + ' coins',
      'Atk ×' + atk.toFixed(2) + '  Sp ×' + spa.toFixed(2) + '  Spd ' + Math.round(pl.spd),
      'Crit ' + pct(pl.critChance || 0) + '  DR ' + pct(pl.reduction || 0) + '  CD ' + cd + '  Regen ' + (pl.regen || 0).toFixed(1) + '/s',
      'XP ×' + (pl.xpMul || 1).toFixed(2) + '  Magnet ' + Math.round(pl.magnet || 0),
      it ? 'Item ' + it.name : 'No item'
    ];
    var taken = pl.statsTaken || {};
    var stats = (G.LevelUp && G.LevelUp.STATS) || [];
    var cards = Object.keys(taken).map(function (id) {
      var s = stats.filter(function (x) { return x.id === id; })[0];
      return (s ? s.title : id) + ' ×' + taken[id];
    });
    if (cards.length) lines.push(cards.join('  '));
    var moves = (pl.moves || []).filter(function (m) { return m.kind !== 'buff'; }).map(function (m) {
      return (m.name || m.id) + ' ' + m.lvl;
    });
    if (moves.length) lines.push(moves.join(' · '));
    var ups = upgradeLine();
    if (ups.length) lines.push(ups.join('  '));
    var rec = records[String(pl.dex)];
    if (rec) {
      var best = rec.bestRun || {};
      lines.push('', 'Record', 'Played ' + rec.played + '  Best ' + clock(rec.best));
      lines.push('Best run Lv ' + (best.level || 1) + (best.item ? '  ' + best.item : ''));
      if (best.moves && best.moves.length) lines.push(best.moves.join(' · '));
      if (best.stats && best.stats.length) lines.push(best.stats.join('  '));
      if (best.upgrades && best.upgrades.length) lines.push(best.upgrades.join('  '));
      if (best.hp) lines.push('Atk ×' + best.atk + '  Sp ×' + best.spa + '  Spd ' + best.spd + '  HP ' + best.hp);
    }
    var box = panel.querySelector('.v');
    box.textContent = '';
    lines.forEach(function (line) {
      var row = document.createElement('div');
      if (!line) row.style.height = '6px';
      else if (line === 'This run' || line === 'Record') row.style.cssText = 'color:#9ec0ff;font-size:11px';
      row.textContent = line;
      box.appendChild(row);
    });
  }

  function showNote(cmd, ms) {
    if (!cmd || cmd.action === 'wait') return;
    var note = cmd.note || {
      source: 'rule',
      question: cmd.action === 'card' ? 'Which option should the Pokémon take?' : 'Which way should the Pokémon move to stay alive?',
      result: cmd.label || '',
      scene: ''
    };
    var panel = ensurePanel();
    panel.querySelector('.q').textContent = note.question || '';
    panel.querySelector('.s').textContent = note.scene || '';
    var answer = note.result || '';
    if (note.raw) answer = note.raw + ' → ' + answer;
    var who = note.source === 'laya' ? 'Laya: ' : 'Rule: ';
    panel.querySelector('.a').textContent = who + answer;
    panel.querySelector('.t').textContent = ms + ' ms';
    var key = (note.source || '') + '|' + (note.question || '');
    if (cmd.action === 'card') key += '|' + (note.result || '') + '|' + (note.raw || '');
    if (key === logKey) return;
    logKey = key;
    var line = document.createElement('div');
    line.style.cssText = 'padding:4px 0;border-top:1px solid rgba(255,255,255,.12);color:#d5e2ff';
    line.textContent = ms + ' ms · ' + who + answer;
    var hist = panel.querySelector('.h');
    hist.insertBefore(line, hist.firstChild);
    while (hist.children.length > 12) hist.removeChild(hist.lastChild);
  }

  function apply(cmd, snap) {
    if (!cmd || cmd.action === 'wait') return;
    if (cmd.action === 'card') {
      var now = phase();
      if (now !== 'levelup' && now !== 'item' && now !== 'bond') return;
      var sig = (snap.cards || []).map(function (c) { return c.label; }).join('|');
      if (cardSent === sig) return;
      cardSent = sig;
      releaseMove();
      tap('Digit' + ((cmd.index || 0) + 1));
      setTimeout(function () {
        if (cardSent === sig && phase() !== 'playing') cardSent = '';
      }, 800);
      return;
    }
    if (cmd.action === 'move' && phase() === 'playing') holdMove(cmd.label);
  }

  function tick() {
    paintVars();
    var ph = phase();
    if (ph === 'playing') { cardSent = ''; useBestAttack(); trackPos(); wander(); }
    else { trail = []; spot = null; nudge = ''; }
    if (ph === 'cinematic') { tap('Space'); return; }
    if (ph === 'over') {
      // The distortion world is still this run. Falling there sends you back
      // to the map; it does not end the run. A game-over screen during that
      // trip used to click "play again" and start a new one.
      if (G.Rift && G.Rift.inArena) return;
      if (overHandled) return;
      overHandled = true;
      rememberRun();
      releaseMove();
      var btn = document.getElementById('btn-again');
      if (btn) btn.click();
      try {
        var next = tune.randomMon ? randomMon() : null;
        if (next) begin(next, runSetup.shiny, maxLevels());
        else G.Flow.play();
      } catch (e) { console.error('[bot]', e); }
      return;
    }
    overHandled = false;
    if (ph !== 'playing' && ph !== 'levelup' && ph !== 'item' && ph !== 'bond') return;
    var box = cardBox(ph);
    if (ph !== 'playing' && box && box.classList.contains('guard')) { releaseMove(); return; }
    var pl = G.Game.player;
    if (ph === 'playing' && pl && Date.now() - mark > 2000) {
      mark = Date.now();
      console.log('[bot] at', Math.round(pl.x), Math.round(pl.y), 'hp', Math.round(pl.hp));
    }
    if (busy) return;
    var snap = snapshot(ph);
    var sent = performance.now();
    busy = true;
    fetch('/decide', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(snap)
    }).then(function (r) {
      if (!r.ok) throw new Error('decide ' + r.status);
      return r.json();
    }).then(function (cmd) {
      showNote(cmd, Math.round(performance.now() - sent));
      apply(cmd, snap);
    }).catch(function (e) {
      console.warn('[bot]', e);
    }).then(function () { busy = false; });
  }

  function resolveMon(q) {
    q = (q || '').trim().toLowerCase();
    if (!q || !G.DEX) return null;
    if (/^\d+$/.test(q)) return G.DEX_BY[+q] || null;
    var exact = G.DEX.filter(function (m) { return m.name.toLowerCase() === q; });
    if (exact.length === 1) return exact[0];
    var starts = G.DEX.filter(function (m) { return m.name.toLowerCase().indexOf(q) === 0; });
    return starts.length === 1 ? starts[0] : null;
  }

  function randomMon() {
    var pool = (G.DEX || []).filter(function (m) {
      return G.SPRITE_META && G.SPRITE_META[m.dex] && !m.leg && m.dex !== runSetup.dex;
    });
    return pool.length ? pool[(Math.random() * pool.length) | 0] : null;
  }

  function begin(mon, shiny, levels) {
    runSetup = { dex: mon.dex, shiny: !!shiny, levels: levels || {} };
    G.DB.playAsGuest();
    var save = G.DB.save;
    save.starter = mon.dex;
    save.partner = mon.dex;
    G.DB.grant(mon.dex, 'starter');
    var canShiny = !!(G.Sprites && G.Sprites.hasShiny && G.Sprites.hasShiny(mon.dex));
    save.partnerShiny = !!(shiny && canShiny);
    if (save.partnerShiny) G.DB.grantShiny(mon.dex, 'bot');
    var ups = G.DB.upgradesOf(mon.dex);
    (G.Upgrades ? G.Upgrades.LIST : []).forEach(function (u) {
      var n = Math.max(0, Math.min(u.max, levels[u.id] | 0));
      if (n) ups[u.id] = n;
      else delete ups[u.id];
    });
    G.Flow.goMenu();
    G.Flow.play();
    console.log('[bot] run', mon.name, save.partnerShiny ? 'shiny' : '', G.Game.state);
  }

  function screen(html) {
    var scr = document.createElement('section');
    scr.className = 'scr';
    scr.innerHTML = html;
    document.body.appendChild(scr);
    return scr;
  }

  function ask(then) {
    var preset = resolveMon(new URLSearchParams(location.search).get('mon'));
    var scr = screen(
      '<div class="box" style="width:min(420px,100%)">' +
      '<h2>Which Pokémon?</h2>' +
      '<p class="sub">Type a name or a Pokédex number</p>' +
      '<input id="bot-mon" class="field" placeholder="Pikachu or 25" autocomplete="off" spellcheck="false">' +
      '<p id="bot-mon-err" class="err"></p>' +
      '<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:10px">' +
      '<label class="sub" style="display:flex;gap:8px;align-items:center;margin:0">' +
      '<input id="bot-shiny" type="checkbox"> Shiny</label>' +
      '<button id="bot-mon-rand" class="btn small" type="button">Random Pokémon</button>' +
      '<button id="bot-mon-ok" class="btn gold" type="button">Next</button></div></div>'
    );
    var input = document.getElementById('bot-mon');
    var err = document.getElementById('bot-mon-err');
    var shinyBox = document.getElementById('bot-shiny');
    if (preset) input.value = preset.name;
    function syncShiny() {
      var mon = resolveMon(input.value);
      var can = !!(mon && G.Sprites && G.Sprites.hasShiny && G.Sprites.hasShiny(mon.dex));
      shinyBox.disabled = !!(mon && !can);
      if (!can) shinyBox.checked = false;
    }
    input.addEventListener('input', syncShiny);
    syncShiny();
    document.getElementById('bot-mon-rand').onclick = function () {
      var mon = randomMon();
      if (!mon) return;
      tune.randomMon = true;
      saveTune();
      scr.remove();
      then(mon, shinyBox.checked, maxLevels());
    };
    function go() {
      tune.randomMon = false;
      saveTune();
      var mon = resolveMon(input.value);
      if (!mon) { err.textContent = 'No single Pokémon matches that. Try the full name or its number.'; return; }
      var shiny = shinyBox.checked;
      scr.remove();
      askStats(mon, shiny, then);
    }
    document.getElementById('bot-mon-ok').onclick = go;
    input.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      e.stopPropagation();
      go();
    });
    input.focus();
  }

  function askStats(mon, shiny, then) {
    var list = (G.Upgrades && G.Upgrades.LIST) || [];
    var rows = list.map(function (u) {
      return '<div class="sub" style="display:flex;gap:8px;align-items:center;margin:8px 0">' +
        '<span style="flex:1"><b>' + u.name + '</b> ' + u.unit + '</span>' +
        '<button type="button" class="btn small" data-max="' + u.id + '">Max</button>' +
        '<input class="field" data-stat="' + u.id + '" type="number" min="0" max="' + u.max + '" value="0" style="width:76px">' +
        '</div>';
    }).join('');
    var scr = screen(
      '<div class="box" style="width:min(480px,100%);max-height:calc(100dvh - 24px);overflow:auto">' +
      '<h2>Stats</h2>' +
      '<p class="sub">' + mon.name + (shiny ? ' shiny' : '') + '. Levels for this run. Coins stay untouched.</p>' +
      '<button id="bot-stat-max" class="btn small" type="button">Max all</button>' +
      rows +
      '<p id="bot-stat-err" class="err"></p>' +
      '<button id="bot-stat-ok" class="btn gold" type="button">Play</button></div>'
    );
    function setMax(id) {
      scr.querySelectorAll('[data-stat]').forEach(function (el) {
        if (!id || el.getAttribute('data-stat') === id) el.value = el.max;
      });
    }
    document.getElementById('bot-stat-max').onclick = function () { setMax(); };
    scr.querySelectorAll('[data-max]').forEach(function (btn) {
      btn.onclick = function () { setMax(btn.getAttribute('data-max')); };
    });
    function go() {
      var levels = {};
      var bad = false;
      scr.querySelectorAll('[data-stat]').forEach(function (el) {
        var u = G.Upgrades.BY[el.getAttribute('data-stat')];
        var n = el.value === '' ? 0 : Number(el.value);
        if (!Number.isInteger(n) || n < 0 || n > u.max) bad = true;
        levels[u.id] = n;
      });
      if (bad) {
        document.getElementById('bot-stat-err').textContent = 'Each stat has to be a whole number from 0 up to its max.';
        return;
      }
      scr.remove();
      then(mon, shiny, levels);
    }
    document.getElementById('bot-stat-ok').onclick = go;
    scr.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      e.stopPropagation();
      go();
    });
  }

  function arm() {
    if (!window.G || !G.DB || !G.Flow || !G.Game || !G.UI || !G.DEX) { setTimeout(arm, 50); return; }
    var boot = document.getElementById('boot');
    if (boot && !boot.classList.contains('hidden')) { setTimeout(arm, 50); return; }
    var tapEl = document.getElementById('tap-start');
    if (tapEl && !tapEl.classList.contains('hidden')) { tapEl.click(); setTimeout(arm, 50); return; }
    pullRecords();
    ask(function (mon, shiny, levels) {
      if (G.Game.state === 'ui') {
        try { begin(mon, shiny, levels); }
        catch (e) { console.error('[bot] start', e); return; }
      }
      if (!loop) { loop = true; setInterval(tick, 200); }
    });
  }

  arm();
})();
