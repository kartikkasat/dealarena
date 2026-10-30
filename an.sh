#!/usr/bin/env bash
# Run from ~/i/dealarena: bash fixui.sh
# Fixes both: (1) hover tooltips on info icons — instant, with content on every field
#             (2) visible unit pill next to every numeric input (EURm / % / x / auctions / seed)
# Idempotent. Safe to re-run. Commits to main.
set -e

for f in index.html css/app.css js/app.js; do
  [ -f "$f" ] || { echo "MISSING: $f"; exit 1; }
done

# un-delete CHANGES.txt if the working tree is otherwise clean-ish
git restore CHANGES.txt 2>/dev/null || true

echo "=== 1. index.html: instant tooltips + hint content + units ==="
python3 - <<'PY'
import re
from pathlib import Path

p = Path("index.html"); s = p.read_text()
changes = 0

def rep1(s, old, new, label):
    global changes
    n = s.count(old)
    if n == 0:
        if new in s:
            print(f"[SKIP] {label}: already applied"); return s
        print(f"[FAIL] {label}: anchor not found"); return s
    if n != 1:
        print(f"[FAIL] {label}: {n} matches (expected 1)"); return s
    print(f"[OK]   {label}"); changes += 1
    return s.replace(old, new, 1)

# ---- A. remove native slow tooltip from label wrapper
s = rep1(s,
    '<label class="f" :title="hint || null">',
    '<label class="f">',
    "label wrapper: drop native :title")

# ---- B. icon: :title -> :data-tip
s = rep1(s,
    '<i v-if="hint" class="info" aria-hidden="true" :title="hint">i</i>',
    '<i v-if="hint" class="info" aria-hidden="true" :data-tip="hint">i</i>',
    "info icon: :title -> :data-tip")

# ---- C. weight sliders: :title -> :data-tip
s = rep1(s,
    '<div class="rng" v-for="w in weightDefs" :key="w.key" :title="w.hint">',
    '<div class="rng" v-for="w in weightDefs" :key="w.key" :data-tip="w.hint">',
    "weight sliders: :title -> :data-tip")

# ---- D. upgrade weak "User assumption" hint on Synergies PV
s = rep1(s,
    'label="Synergies (PV)" unit="EURm" v-model="sc.acquirer.synergies" :min="0" hint="User assumption"',
    'label="Synergies (PV)" unit="EURm" v-model="sc.acquirer.synergies" :min="0" hint="Present value of cost savings and revenue synergies you believe are realisable"',
    "Synergies PV: hint upgraded")

# ---- E. ensure every listed field has BOTH unit= and hint=
def find_tag(text, label):
    return re.search(r'<num-field\b[^>]*\blabel="' + re.escape(label) + r'"[^>]*>', text)

def ensure_attr(tag, attr, value):
    if re.search(rf'\b{attr}="[^"]*"', tag):
        return tag
    return tag[:-1] + f' {attr}="{value}">'

specs = [
    # label,                  unit,       hint
    ("Simulated auctions",    "auctions", "Number of randomised auction outcomes. 10,000 runs in about a second; 50,000 reduces noise."),
    ("Fixed seed",            "seed",     None),  # hint already exists
    ("Board walk-away",       "EURm",     "Maximum bid approved by the board. The engine never recommends above this."),
    ("Committed financing",   "EURm",     "Cash plus committed debt available for the bid. Hard ceiling."),
    ("Their synergies",       "%",        "Estimated synergies as % of the target's standalone value, if this rival wins."),
    ("Financing limit",       "EURm",     "Maximum this rival can bid given their own financing capacity."),
    ("Target IRR",            "%",        "Return this sponsor needs on equity. Higher IRR means a lower reservation price."),
    ("Leverage",              "x",        "Debt as a multiple of EBITDA the sponsor can raise. Drives their reservation price."),
    ("Exit multiple",         "x",        "EV/EBITDA multiple the sponsor expects to exit at."),
    ("EBITDA uplift",         "%",        "Operational EBITDA improvement the sponsor expects during the hold."),
    ("Max equity",            "EURm",     "Maximum equity cheque the sponsor can write. With leverage, sets their financing cap."),
    ("Valuation noise",       "%",        "Std dev of the rival's valuation signal. Higher means more variance in their bid."),
    ("Fever probability",     "%",        "Chance the rival loses discipline and bids above their own valuation."),
]

for label, unit, hint in specs:
    m = find_tag(s, label)
    if not m:
        print(f"[FAIL] tag not found: {label}"); continue
    old_tag = m.group(0)
    new_tag = ensure_attr(old_tag, 'unit', unit)
    if hint:
        new_tag = ensure_attr(new_tag, 'hint', hint)
    if new_tag == old_tag:
        print(f"[SKIP] {label}: already has unit{' + hint' if hint else ''}")
    else:
        s = s.replace(old_tag, new_tag, 1)
        print(f"[OK]   {label}: unit={unit}{' + hint' if hint else ''}")
        changes += 1

p.write_text(s)
print(f"\nindex.html: {changes} change(s)")
PY

echo
echo "=== 2. css/app.css: pointer cursor + visible unit pill ==="
python3 - <<'PY'
import re
from pathlib import Path

p = Path("css/app.css"); c = p.read_text()

# --- cursor: help -> pointer (this is the "?" you saw on hover)
n = c.count('cursor: help')
if n:
    c = c.replace('cursor: help', 'cursor: pointer')
    print(f"[OK]   cursor: help -> pointer ({n} rule(s))")
else:
    print("[SKIP] cursor already pointer")

# --- strip any old .f-i em rule so we control the final one
c = re.sub(r'\n\.f-i\s+em\s*\{[^}]*\}', '', c)

# --- ensure .f-i lays out input + unit side by side
if not re.search(r'\.f-i\s*\{[^}]*display\s*:\s*flex', c):
    c += (
        "\n/* numeric field: input + visible unit pill on one row */\n"
        ".f-i { display: flex; align-items: center; gap: 6px; }\n"
        ".f-i input { flex: 1 1 auto; min-width: 0; }\n"
    )
    print("[OK]   .f-i flex layout added")
else:
    print("[SKIP] .f-i already flex")

# --- the pill style itself (appended last so it wins the cascade)
c += (
    "\n.f-i em { flex: 0 0 auto; font-style: normal; font-weight: 600; "
    "font-size: 11px; line-height: 1; color: #5A6577; white-space: nowrap; "
    "background: #EDF0F4; padding: 4px 7px; border-radius: 4px; "
    "letter-spacing: 0.01em; user-select: none; }\n"
)
print("[OK]   .f-i em pill styled")

p.write_text(c)
PY

echo
echo "=== 3. js/app.js: instant tooltip handler ==="
python3 - <<'PY'
from pathlib import Path

p = Path("js/app.js"); s = p.read_text()
if 'da-tip' in s:
    print("[SKIP] tooltip handler already present")
else:
    marker = "  app.mount('#app');\n"
    assert s.count(marker) == 1, f"marker count={s.count(marker)}"
    tip = '''
  // ---------- instant tooltip for [data-tip] ----------
  (function () {
    var el = null, timer = null;
    function ensure() {
      if (el) return el;
      el = document.createElement('div');
      el.id = 'da-tip';
      el.style.cssText = 'position:fixed;background:#1F2A44;color:#fff;padding:6px 10px;'
        + 'border-radius:6px;font-size:12px;line-height:1.35;max-width:260px;text-align:center;'
        + 'z-index:99999;pointer-events:none;box-shadow:0 4px 12px rgba(0,0,0,.25);'
        + 'opacity:0;transition:opacity .1s;font-family:inherit;';
      document.body.appendChild(el);
      return el;
    }
    function show(target) {
      var t = ensure();
      t.textContent = target.getAttribute('data-tip');
      t.style.display = 'block';
      var r = target.getBoundingClientRect();
      var tr = t.getBoundingClientRect();
      var l = r.left + r.width / 2 - tr.width / 2;
      var top = r.top - tr.height - 8;
      if (l < 6) l = 6;
      if (l + tr.width > window.innerWidth - 6) l = window.innerWidth - tr.width - 6;
      if (top < 6) top = r.bottom + 8;
      t.style.left = l + 'px';
      t.style.top = top + 'px';
      t.style.opacity = '1';
    }
    function hide() {
      if (el) el.style.opacity = '0';
    }
    document.addEventListener('mouseover', function (e) {
      var target = e.target.closest && e.target.closest('[data-tip]');
      if (!target) return;
      clearTimeout(timer);
      timer = setTimeout(function () { show(target); }, 40);
    });
    document.addEventListener('mouseout', function (e) {
      var target = e.target.closest && e.target.closest('[data-tip]');
      if (!target) return;
      clearTimeout(timer);
      hide();
    });
    document.addEventListener('scroll', hide, true);
    window.addEventListener('blur', hide);
  })();
'''
    s = s.replace(marker, marker + tip, 1)
    print("[OK]   tooltip handler added")
    p.write_text(s)
PY

echo
echo "=== 4. git diff --stat ==="
git diff --stat
if git diff --quiet; then
  echo "No changes to commit."
  exit 0
fi

echo "=== 5. commit + push (main) ==="
git add -A
git commit -m "Instant info-icon tooltips with full hint content; visible unit pill on every numeric field"
git push origin main
git log --oneline -1
