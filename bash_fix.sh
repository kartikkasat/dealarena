#!/usr/bin/env bash
# Run from repo root: bash fix.sh
# Commits to main. No branches, no set -e, verbose at every step.

echo "=== pwd ==="
pwd

echo "=== file check ==="
for f in backend/engine.py backend/app.py index.html css/app.css js/app.js; do
  if [ ! -f "$f" ]; then
    echo "MISSING: $f"
    echo "--- dir listing for $(dirname "$f") ---"
    ls -la "$(dirname "$f")" 2>/dev/null || true
    echo "Aborting."
    exit 1
  fi
  echo "  found: $f"
done

echo "=== un-delete CHANGES.txt ==="
git restore CHANGES.txt 2>/dev/null || true
git status --short

echo "=== applying patches ==="
python3 -u <<'PY'
import sys
from pathlib import Path

def patch(path, label, fn):
    p = Path(path); src = p.read_text(); new = fn(src)
    if new is None or new == src:
        print(f"[FAIL] {label}: no change"); sys.exit(1)
    p.write_text(new)
    print(f"[OK]   {label}: {len(src)} -> {len(new)} bytes ({len(new)-len(src):+d})")

def e1(s):
    o = "    comp_bids = np.zeros((K, N))\n    top = np.zeros(N)\n"
    assert s.count(o) == 1, f"e1 count={s.count(o)}"
    return s.replace(o, o + "    _eval_cache: dict = {}\n", 1)
def e2(s):
    o = ('    def apply_shading(sh):\n        nonlocal top\n        for j in range(K):\n'
         '            sj = 1.0 if d["format"] == "english" else sh[j]\n'
         '            comp_bids[j] = np.maximum(0, np.minimum(resv[j] * sj * fev[j], caps[j]))\n'
         '        top = comp_bids.max(axis=0)\n')
    n = ('    def apply_shading(sh):\n        nonlocal top\n        _prev_top = top\n        for j in range(K):\n'
         '            sj = 1.0 if d["format"] == "english" else sh[j]\n'
         '            comp_bids[j] = np.maximum(0, np.minimum(resv[j] * sj * fev[j], caps[j]))\n'
         '        top = comp_bids.max(axis=0)\n'
         '        if not np.array_equal(top, _prev_top):\n            _eval_cache.clear()\n')
    assert s.count(o) == 1, f"e2 count={s.count(o)}"
    return s.replace(o, n, 1)
def e3(s):
    a = "    def feasibility(r):\n"
    shim = ('    _eval_orig = evaluate\n'
            '    def evaluate(bid: float, keep_dist: bool = False) -> dict:\n'
            '        if keep_dist:\n            return _eval_orig(bid, True)\n'
            '        if bid in _eval_cache:\n            return copy.deepcopy(_eval_cache[bid])\n'
            '        _r = _eval_orig(bid, False)\n        _eval_cache[bid] = copy.deepcopy(_r)\n'
            '        return _r\n\n')
    assert s.count(a) == 1, f"e3 count={s.count(a)}"
    return s.replace(a, shim + a, 1)
def e4(s):
    o = ('def financing_cap(c: dict, ebitda: float) -> float:\n'
         '    return c["leverage"] * ebitda + c["maxEquity"] if c["type"] == "pe" else c["financingLimit"]\n')
    n = ('def financing_cap(c: dict, ebitda: float) -> float:\n'
         '    if c["type"] == "pe":\n        return c["leverage"] * ebitda + c.get("maxEquity", 0)\n'
         '    return c["financingLimit"]\n')
    assert s.count(o) == 1, f"e4 count={s.count(o)}"
    return s.replace(o, n, 1)
def a1(s):
    o = "    if isinstance(x, (list, tuple)):\n        return [_clean(v) for v in x]\n"
    n = o + "    if isinstance(x, np.ndarray):\n        return [_clean(v) for v in x.tolist()]\n"
    assert s.count(o) == 1, f"a1 count={s.count(o)}"
    return s.replace(o, n, 1)
def c1(s):
    o = ("i.info { font-style: normal; font-size: 10px; width: 14px; height: 14px; "
         "border-radius: 50%; border: 1px solid var(--ink-3); color: var(--ink-3); "
         "display: inline-flex; align-items: center; justify-content: center; cursor: help; }")
    n = ("i.info { font-family: system-ui, -apple-system, 'Segoe UI', sans-serif; "
         "font-style: normal; font-weight: 700; font-size: 10px; line-height: 1; "
         "width: 14px; height: 14px; border-radius: 50%; border: 1px solid var(--ink-3); "
         "color: var(--ink-3); display: inline-flex; align-items: center; justify-content: center; "
         "cursor: help; user-select: none; }")
    assert s.count(o) == 1, f"c1 count={s.count(o)}"
    return s.replace(o, n, 1)
def h1(s):
    o = '<i v-if="hint" class="info" aria-hidden="true">i</i>'
    n = '<i v-if="hint" class="info" aria-hidden="true" :title="hint">i</i>'
    assert s.count(o) == 1, f"h1 count={s.count(o)}"
    return s.replace(o, n, 1)
def j1(s):
    o = "var wait = Math.max(0, (manual ? 300 : 0) - (Date.now() - t0));"
    n = "var wait = Math.max(0, (manual ? 120 : 0) - (Date.now() - t0));"
    assert s.count(o) == 1, f"j1 count={s.count(o)}"
    return s.replace(o, n, 1)

patch("backend/engine.py", "1. _eval_cache var",           e1)
patch("backend/engine.py", "2. cache clear on top change", e2)
patch("backend/engine.py", "3. memo shim on evaluate()",   e3)
patch("backend/engine.py", "4. financing_cap defensive",   e4)
patch("backend/app.py",    "5. _clean ndarray",            a1)
patch("css/app.css",       "6. .info icon font-safe",      c1)
patch("index.html",        "7. :title on info icon",       h1)
patch("js/app.js",         "8. manual wait 300->120ms",    j1)
PY

echo "=== git diff --stat ==="
git diff --stat

if git diff --quiet; then
  echo "FAIL: no changes were made. Nothing to commit."
  exit 1
fi

echo "=== git add + commit (main) ==="
git add -A
git commit -m "Engine memoization, defensive financing_cap, UI info icon fix, faster manual runs"

echo "=== git log ==="
git log --oneline -1

echo
echo "Done. To push:  git push origin main"
