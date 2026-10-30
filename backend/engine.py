"""DealArena simulation engine (server side).

Rule-based Monte Carlo auction simulator with optional adaptive rivals (iterated best
response). It is NOT a trained multi-agent reinforcement learning model.

Design notes
- All monetary values are EUR millions (EURm).
- Random draws come from the same Mulberry32 generator and Box-Muller transform, drawn in
  the same order, as the original browser engine, so a given seed reproduces the figures
  quoted in the deck. The heavy lifting after the draws is vectorised with NumPy.
- The system generates the bid. A deal-team proposal is optional and only used for comparison.
"""
from __future__ import annotations

import copy
import math
import secrets
import time

import numpy as np

VERSION = "1.0.0"
M32 = 0xFFFFFFFF


# --------------------------------------------------------------------------- random numbers
class Mulberry32:
    """Bit-exact port of the JavaScript Mulberry32 generator."""

    def __init__(self, seed: int):
        self.a = seed & M32

    def rand(self) -> float:
        self.a = (self.a + 0x6D2B79F5) & M32
        t = self.a
        t = ((t ^ (t >> 15)) * (t | 1)) & M32
        t = t ^ ((t + (((t ^ (t >> 7)) * (t | 61)) & M32)) & M32)
        return ((t ^ (t >> 14)) & M32) / 4294967296.0


class Normal:
    """Box-Muller with a cached spare value, as in the browser engine."""

    def __init__(self, rng: Mulberry32):
        self.rng = rng
        self.spare = None

    def __call__(self) -> float:
        if self.spare is not None:
            s, self.spare = self.spare, None
            return s
        u = 0.0
        v = 0.0
        while u == 0.0:
            u = self.rng.rand()
        while v == 0.0:
            v = self.rng.rand()
        r = math.sqrt(-2.0 * math.log(u))
        th = 2.0 * math.pi * v
        self.spare = r * math.sin(th)
        return r * math.cos(th)


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def js_round(x: float) -> int:
    return int(math.floor(x + 0.5))


# --------------------------------------------------------------------------- sample case
def default_scenario() -> dict:
    return {
        "deal": {"name": "Project Atlas (hypothetical)", "ebitda": 100, "standaloneValue": 1000,
                 "valueUncertainty": 8, "format": "sealed", "increment": 1.0},
        "acquirer": {"proposedBid": 1180, "synergies": 200, "synergyRealisation": 75,
                     "synergyRealisationSd": 20, "strategicValue": 60, "emotionalValue": 25},
        "limits": {"walkAway": 1200, "financing": 1350, "requireNonNegativeNpv": True,
                   "minNpv": 0, "maxTailLoss": 200},
        "weights": {"financial": 100, "strategic": 50, "emotional": 20, "risk": 50},
        "sim": {"runs": 10000, "seed": 20261030, "randomSeed": True, "gridPoints": 61, "adaptive": False},
        "competitors": [
            {"id": "c1", "name": "Strategic A (Nordic peer)", "type": "strategic", "enabled": True,
             "synergyPct": 8, "signalNoise": 7, "financingLimit": 1250, "shading": 95,
             "feverProb": 10, "feverPremium": 8, "observedBid": None},
            {"id": "c2", "name": "Strategic B (US conglomerate)", "type": "strategic", "enabled": True,
             "synergyPct": 5, "signalNoise": 9, "financingLimit": 1500, "shading": 93,
             "feverProb": 15, "feverPremium": 10, "observedBid": None},
            {"id": "c3", "name": "PE Sponsor C (large-cap fund)", "type": "pe", "enabled": True,
             "targetIrr": 18, "holdYears": 5, "ebitdaGrowth": 6, "ebitdaUplift": 8, "exitMultiple": 10,
             "leverage": 5, "debtPaydown": 30, "maxEquity": 600, "signalNoise": 6, "shading": 97,
             "feverProb": 0, "feverPremium": 0, "observedBid": None},
        ],
    }


# Parameter ranges the rules were designed for; values outside are flagged out-of-distribution.
RANGES = {
    "strategic": {"synergyPct": (0, 25, "synergies as % of standalone value"),
                  "signalNoise": (3, 25, "valuation noise"),
                  "shading": (80, 100, "bid as % of own valuation"),
                  "feverProb": (0, 30, "probability of auction fever"),
                  "feverPremium": (0, 20, "auction fever premium")},
    "pe": {"targetIrr": (12, 30, "target IRR"),
           "leverage": (2, 7, "leverage (x EBITDA)"),
           "signalNoise": (3, 25, "valuation noise"),
           "shading": (80, 100, "bid as % of own valuation"),
           "ebitdaUplift": (0, 25, "EBITDA improvement"),
           "feverProb": (0, 15, "probability of auction fever")},
}


# Model card for the rival model, following the AI implementation pipeline of the presentation
# (slide 11: data, calibration, training, validation, serving). It states what exists today and what is planned.
RIVAL_MODEL = {
    "id": "rivals-rules-1.0",
    "name": "Rule-based rivals with optional iterated best response",
    "isTrainedMarl": False,
    "parameters": "Illustrative values inside the ranges below; not calibrated on transaction data",
    "trainingData": None,
    "clientDataUsedForTraining": False,
    "liveLearning": False,
    "pipeline": [
        {"stage": "Data", "status": "planned",
         "today": "No deal data; user inputs only",
         "plan": "Bid histories from 'Background of the merger' sections of SEC filings; licensed deal data (PitchBook, Capital IQ)"},
        {"stage": "Calibration", "status": "planned",
         "today": "Parameters set by hand inside plausible ranges; values outside them are flagged",
         "plan": "Fit rival types so simulated bid counts, bid jumps and premiums match the filed histories"},
        {"stage": "Training", "status": "planned",
         "today": "No training; rivals follow rules or adapt by iterated best response",
         "plan": "Self-play in this simulator; one neural bid function per rival type, trained with NPGA (Bichler et al. 2021)"},
        {"stage": "Validation", "status": "partly in place",
         "today": "Automated checks; adaptive rivals report the most any rival could gain by deviating (exploitability)",
         "plan": "Held-out deals: win-chance calibration (Brier score), exploitability, must beat the rule-based rivals"},
        {"stage": "Serving", "status": "in place",
         "today": "Versioned, seeded engine; every run logged with seed, input hash and model id; no live learning",
         "plan": "Frozen, versioned trained agents served by the same engine"},
    ],
    "parameterRanges": {t: {k: {"low": v[0], "high": v[1], "meaning": v[2]} for k, v in r.items()} for t, r in RANGES.items()},
}


def pe_reservation(c: dict, ebitda: float, signal_factor):
    """Highest price at which equity (price minus debt) earns the target IRR at exit.
    Works on a float or a NumPy array of signal factors."""
    D = c["leverage"] * ebitda
    n = max(1, c["holdYears"])
    exit_ebitda = ebitda * (1 + c["ebitdaUplift"] / 100) * (1 + c["ebitdaGrowth"] / 100) ** n
    exit_ev = exit_ebitda * c["exitMultiple"] * signal_factor
    debt_exit = D * (1 - c["debtPaydown"] / 100)
    exit_equity = np.maximum(0, exit_ev - debt_exit)
    return D + exit_equity / (1 + c["targetIrr"] / 100) ** n


def financing_cap(c: dict, ebitda: float) -> float:
    if c["type"] == "pe":
        return c["leverage"] * ebitda + c.get("maxEquity", 0)
    return c["financingLimit"]


def has_proposal(a: dict) -> bool:
    p = a.get("proposedBid")
    return p is not None and p > 0


def validate(s: dict) -> list[str]:
    errors = []
    d, l = s["deal"], s["limits"]
    if not d["standaloneValue"] > 0:
        errors.append("Standalone value must be positive.")
    if not d["ebitda"] > 0:
        errors.append("EBITDA must be positive.")
    if not l["walkAway"] > 0:
        errors.append("Walk-away maximum must be positive.")
    if not l["financing"] > 0:
        errors.append("Financing capacity must be positive.")
    if not 500 <= s["sim"]["runs"] <= 50000:
        errors.append("Number of simulated auctions must be between 500 and 50,000.")
    if not 0 <= d["valueUncertainty"] <= 60:
        errors.append("Value uncertainty must be between 0% and 60%.")
    if not any(c.get("enabled", True) for c in s["competitors"]):
        errors.append("Enable at least one competitor.")
    return errors


# --------------------------------------------------------------------------- core simulation
def run(scenario: dict, seed: int | None = None) -> dict:
    """Run one simulation. If `seed` is given it overrides the scenario (used for replays)."""
    t0 = time.perf_counter()
    s = copy.deepcopy(scenario)
    errors = validate(s)
    if errors:
        return {"ok": False, "errors": errors}

    d, a, L, W = s["deal"], s["acquirer"], s["limits"], s["weights"]
    N = js_round(s["sim"]["runs"])
    if seed is None:
        seed = secrets.randbelow(10 ** 9) if s["sim"].get("randomSeed") else int(s["sim"]["seed"]) & M32
    seed = int(seed) & M32
    rng = Mulberry32(seed)
    norm = Normal(rng)
    V, sigV = float(d["standaloneValue"]), d["valueUncertainty"] / 100
    comps = [c for c in s["competitors"] if c.get("enabled", True)]
    K = len(comps)
    prop = float(a["proposedBid"]) if has_proposal(a) else None

    # ---- draws, in exactly the order of the browser engine
    z_v = np.empty(N); z_r = np.empty(N)
    z_s = np.empty((K, N)); u_f = np.empty((K, N))
    for k in range(N):
        z_v[k] = norm()
        z_r[k] = norm()
        for j in range(K):
            z_s[j, k] = norm()
            u_f[j, k] = rng.rand()

    v_true = V * np.exp(sigV * z_v - 0.5 * sigV * sigV)
    syn_real = a["synergies"] * np.clip(a["synergyRealisation"] / 100 + (a["synergyRealisationSd"] / 100) * z_r, 0, 1.3)
    resv = np.empty((K, N)); tval = np.empty((K, N)); fev = np.empty((K, N))
    caps = np.array([financing_cap(c, d["ebitda"]) for c in comps], dtype=float)
    for j, c in enumerate(comps):
        sig = c["signalNoise"] / 100
        signal = (v_true / V) * np.exp(sig * z_s[j] - 0.5 * sig * sig)
        if c["type"] == "pe":
            resv[j] = pe_reservation(c, d["ebitda"], signal)
            tval[j] = pe_reservation(c, d["ebitda"], v_true / V)
        else:
            resv[j] = V * signal * (1 + c["synergyPct"] / 100)
            tval[j] = v_true * (1 + c["synergyPct"] / 100)
        fev[j] = np.where(u_f[j] < c["feverProb"] / 100, 1 + c["feverPremium"] / 100, 1.0)

    comp_bids = np.zeros((K, N))
    top = np.zeros(N)
    _eval_cache: dict = {}

    def apply_shading(sh):
        nonlocal top
        _prev_top = top
        for j in range(K):
            sj = 1.0 if d["format"] == "english" else sh[j]
            comp_bids[j] = np.maximum(0, np.minimum(resv[j] * sj * fev[j], caps[j]))
        top = comp_bids.max(axis=0)
        if not np.array_equal(top, _prev_top):
            _eval_cache.clear()

    static_sh = [c["shading"] / 100 for c in comps]
    apply_shading(static_sh)
    mean_syn = float(syn_real.mean())

    # ---- candidate bid grid
    lo = js_round(0.8 * V)
    hi = max(L["walkAway"], L["financing"], prop or 0) * 1.04
    G = max(21, min(121, int(s["sim"].get("gridPoints", 61))))
    step = (hi - lo) / (G - 1)
    grid = [lo + g * step for g in range(G)]
    for x in [prop, L["walkAway"], V, V + a["synergies"] * a["synergyRealisation"] / 100]:
        if x is not None and lo <= x <= hi:
            grid.append(x)
    grid.sort()
    grid = [x for i, x in enumerate(grid) if i == 0 or abs(x - grid[i - 1]) > 1e-6]

    inc = d["increment"] / 100
    non_fin = (W["strategic"] / 100) * a["strategicValue"] + (W["emotional"] / 100) * a["emotionalValue"]
    wf, wr = W["financial"] / 100, W["risk"] / 100

    def evaluate(bid: float, keep_dist: bool = False) -> dict:
        win = bid > top
        if d["format"] == "english":
            price = np.where(win, np.minimum(bid, top * (1 + inc)), 0.0)
        else:
            price = np.full(N, bid)
        m = int(win.sum())
        fv = (v_true + syn_real - price)[win]
        p_win = m / N
        es5 = 0.0
        if m > 0:
            srt = np.sort(fv)
            tail_n = max(1, int(math.floor(0.05 * m)))
            es5 = float(srt[:tail_n].sum() / tail_n)
        sum_fv = float(fv.sum())
        exp_fv_win = sum_fv / m if m else 0.0
        exp_fv = sum_fv / N
        risk_pen = p_win * max(0.0, -es5)
        utility = wf * exp_fv + p_win * non_fin - wr * risk_pen
        u = np.zeros(N)
        u[win] = wf * fv + non_fin
        mean_u = float(u.mean())
        se_u = math.sqrt(max(0.0, float((u * u).mean()) - mean_u * mean_u) / N)
        over = fv < 0
        r = {
            "bid": float(bid), "pWin": p_win, "pWinSe": math.sqrt(p_win * (1 - p_win) / N),
            "expFv": exp_fv, "expFvWin": exp_fv_win,
            "expPriceWin": float(price[win].sum() / m) if m else 0.0,
            "expVWin": float(v_true[win].sum() / m) if m else 0.0,
            "pOverWin": float(over.sum() / m) if m else 0.0,
            "expOverWin": float((-fv[over]).sum() / m) if m else 0.0,
            "es5Win": es5, "utility": utility, "utilitySe": se_u,
            "parts": {"financial": wf * exp_fv,
                      "strategic": p_win * (W["strategic"] / 100) * a["strategicValue"],
                      "emotional": p_win * (W["emotional"] / 100) * a["emotionalValue"],
                      "risk": -wr * risk_pen},
        }
        if keep_dist:
            r["_dist"] = fv
        return r

    _eval_orig = evaluate
    def evaluate(bid: float, keep_dist: bool = False) -> dict:
        if keep_dist:
            return _eval_orig(bid, True)
        if bid in _eval_cache:
            return copy.deepcopy(_eval_cache[bid])
        _r = _eval_orig(bid, False)
        _eval_cache[bid] = copy.deepcopy(_r)
        return _r

    def feasibility(r):
        out = []
        if r["bid"] > L["walkAway"] + 1e-9:
            out.append("above board walk-away maximum")
        if r["bid"] > L["financing"] + 1e-9:
            out.append("above committed financing")
        if L["requireNonNegativeNpv"] and r["pWin"] >= 0.005 and r["expFvWin"] < L["minNpv"]:
            out.append("expected value given a win below minimum")
        if r["pWin"] >= 0.005 and r["es5Win"] < -L["maxTailLoss"]:
            out.append("tail loss above tolerance")
        return out

    def recommend_bid():
        best = None
        for b in grid:
            r = evaluate(b)
            if feasibility(r):
                continue
            if best is None or r["utility"] > best["utility"]:
                best = r
        return best if best and best["utility"] > 0 else None

    # ---- adaptive rivals: iterated best response on bid shading (sealed bid only)
    SH_GRID = [q / 100 for q in range(60, 101, 2)]

    def others_top(j):
        if K == 1:
            return np.zeros(N)
        return np.delete(comp_bids, j, axis=0).max(axis=0)

    def rival_profit(j, sh, other, our_bid):
        b = np.maximum(0, np.minimum(resv[j] * sh * fev[j], caps[j]))
        w = (b > other) & (b > our_bid)
        return float((tval[j] - b)[w].sum() / N), float(w.sum() / N)

    def rival_summary(sh, our_bid):
        out = []
        for j, c in enumerate(comps):
            pr, pw = rival_profit(j, sh[j], others_top(j), our_bid)
            out.append({"id": c["id"], "name": c["name"], "type": c["type"], "shading": sh[j], "profit": pr, "pWin": pw})
        return out

    adaptation = None
    static_rec = recommend_bid()
    if d["format"] == "sealed":
        sh = list(static_sh)
        our = static_rec["bid"] if static_rec else 0.0
        before_rivals = rival_summary(sh, our)
        history = [{"round": 0, "shading": list(sh), "ourBid": our}]
        converged, rounds, seen = False, 0, set()
        for rnd in range(1, 26):
            rounds = rnd
            changed = False
            for j in range(K):
                ot = others_top(j)
                best_s, best_p = sh[j], rival_profit(j, sh[j], ot, our)[0]
                for x in SH_GRID:
                    p_ = rival_profit(j, x, ot, our)[0]
                    if p_ > best_p + 1e-9:
                        best_p, best_s = p_, x
                if abs(best_s - sh[j]) > 1e-9:
                    sh[j] = best_s
                    changed = True
                    apply_shading(sh)
            nr = recommend_bid()
            nb = nr["bid"] if nr else 0.0
            moved = abs(nb - our) > 1e-6
            our = nb
            history.append({"round": rnd, "shading": list(sh), "ourBid": our})
            if not changed and not moved:
                converged = True
                break
            key = ",".join(f"{x:.2f}" for x in sh) + f"|{our:.3f}"
            if key in seen:
                break
            seen.add(key)
        max_gain = 0.0
        for j in range(K):
            ot = others_top(j)
            cur = rival_profit(j, sh[j], ot, our)[0]
            for x in SH_GRID:
                max_gain = max(max_gain, rival_profit(j, x, ot, our)[0] - cur)
        after_rivals = rival_summary(sh, our)
        last = [h["ourBid"] for h in history[-3:]]
        osc = max(last) - min(last)
        status = "converged" if converged else ("near" if (max_gain <= 0.5 and osc <= 2 * step + 1e-6) else "not")
        after_rec = evaluate(our) if our else None
        adaptation = {
            "enabled": bool(s["sim"].get("adaptive")), "converged": converged, "status": status,
            "oscillation": osc, "bidStep": step, "rounds": rounds, "maxRivalGain": max_gain, "history": history,
            "before": {"ourBid": static_rec["bid"] if static_rec else None,
                       "ourPWin": static_rec["pWin"] if static_rec else 0.0,
                       "ourValueIfWin": static_rec["expFvWin"] if static_rec else 0.0, "rivals": before_rivals},
            "after": {"ourBid": our or None, "rivals": after_rivals,
                      "ourPWin": after_rec["pWin"] if after_rec else 0.0,
                      "ourValueIfWin": after_rec["expFvWin"] if after_rec else 0.0},
        }
        if not s["sim"].get("adaptive"):
            apply_shading(static_sh)
        else:
            for j, c in enumerate(comps):
                c["shading"] = js_round(sh[j] * 100)

    # ---- curve and constrained optimum
    curve = []
    for b in grid:
        r = evaluate(b)
        r["violations"] = feasibility(r)
        r["feasible"] = not r["violations"]
        curve.append(r)
    feasible = [r for r in curve if r["feasible"]]
    best_f = max(feasible, key=lambda r: r["utility"]) if feasible else None
    best_all = max(curve, key=lambda r: r["utility"]) if curve else None
    # max() keeps the first maximum, like the browser engine's strict '>' comparison
    walk_away = best_f is None or best_f["utility"] <= 0
    rec = None if walk_away else evaluate(best_f["bid"], keep_dist=True)
    if rec:
        rec["violations"], rec["feasible"] = [], True

    rng_out = None
    if rec:
        tol = max(0.02 * abs(best_f["utility"]), 2 * best_f["utilitySe"], 0.5)
        in_range = [r["bid"] for r in feasible if r["utility"] >= best_f["utility"] - tol]
        rng_out = {"low": min(in_range), "high": max(in_range), "tolerance": tol}

    proposed = None
    if prop is not None:
        proposed = evaluate(prop, keep_dist=True)
        proposed["violations"] = feasibility(proposed)
        proposed["feasible"] = not proposed["violations"]

    # ---- decision thresholds: highest bid each hard limit allows (bisection)
    cache: dict[str, dict] = {}

    def ev(b):
        key = f"{b:.4f}"
        if key not in cache:
            cache[key] = evaluate(b)
        return cache[key]

    def max_bid_where(test, lo_, hi_):
        if not test(lo_):
            return {"value": lo_, "below": True}
        if test(hi_):
            return {"value": hi_, "above": True}
        for _ in range(15):
            mid = (lo_ + hi_) / 2
            if test(mid):
                lo_ = mid
            else:
                hi_ = mid
        return {"value": lo_}

    s_lo = 0.6 * V
    s_hi = max(L["walkAway"], L["financing"], prop or 0, V * 1.3) * 1.6
    floor_max = max_bid_where(lambda b: ev(b)["pWin"] < 0.003 or ev(b)["expFvWin"] >= L["minNpv"], s_lo, s_hi)
    tail_max = max_bid_where(lambda b: ev(b)["pWin"] < 0.003 or ev(b)["es5Win"] >= -L["maxTailLoss"], s_lo, s_hi)
    rows = [
        {"key": "floor", "label": f"Value if we win at least EUR {js_round(L['minNpv'])}m" if L["requireNonNegativeNpv"]
            else "Value if we win at least EUR 0m (switched off)",
         "max": floor_max["value"], "above": bool(floor_max.get("above")), "below": bool(floor_max.get("below")),
         "active": bool(L["requireNonNegativeNpv"])},
        {"key": "tail", "label": f"Worst 5% loss no more than EUR {js_round(L['maxTailLoss'])}m",
         "max": tail_max["value"], "above": bool(tail_max.get("above")), "below": bool(tail_max.get("below")), "active": True},
        {"key": "walk", "label": "Board walk-away maximum", "max": float(L["walkAway"]), "active": True},
        {"key": "fin", "label": "Committed financing", "max": float(L["financing"]), "active": True},
    ]
    binding = min((r for r in rows if r["active"]), key=lambda r: r["max"])
    for r in rows:
        r["binding"] = r is binding
    ceiling = binding["max"]
    at_ceiling = ev(ceiling)

    def synergy_need(r):
        if not r or r["pWin"] < 0.003:
            return None
        needed = r["expPriceWin"] - r["expVWin"] + max(0, L["minNpv"] if L["requireNonNegativeNpv"] else 0)
        return {"bid": r["bid"], "needed": needed, "share": needed / a["synergies"] if a["synergies"] > 0 else None}

    thresholds = {
        "rows": rows, "ceiling": ceiling, "binding": binding["key"], "bindingLabel": binding["label"],
        "winAtCeiling": at_ceiling["pWin"], "valueAtCeiling": at_ceiling["expFvWin"],
        "naiveBreakEven": V + mean_syn, "curseDiscount": (V + mean_syn) - floor_max["value"],
        "floorMax": floor_max["value"], "assumedShare": a["synergyRealisation"] / 100,
        "proposalGap": (prop - ceiling) if prop is not None else None,
        "synergyRec": synergy_need(ev(rec["bid"])) if rec else None,
        "synergyProp": synergy_need(ev(prop)) if prop is not None else None,
    }

    # ---- strategies compared on the same simulated auctions
    break_even = V + a["synergies"] * a["synergyRealisation"] / 100

    def strat(label, desc, bid):
        if bid is None or not bid > 0:
            return None
        r = evaluate(bid)
        r["violations"] = feasibility(r)
        r["feasible"] = not r["violations"]
        r["label"], r["desc"] = label, desc
        return r

    win_max = next((r["bid"] for r in curve if r["pWin"] >= 0.9), None)
    strategies = [
        strat("System bid", "Best weighted outcome within hard limits", rec["bid"]) if rec else
        {"label": "System bid", "desc": "Walk away: no feasible bid creates value", "bid": 0, "pWin": 0, "expFv": 0,
         "expFvWin": 0, "pOverWin": 0, "expOverWin": 0, "es5Win": 0, "utility": 0, "feasible": True,
         "violations": [], "walkAway": True},
        strat("Deal team proposal", "Bid entered for comparison", prop),
        strat("Standalone value", "Pay no synergies away", V),
        strat("Break-even", "Standalone value + expected realised synergies", break_even),
        strat("Win-maximising", "Lowest bid with at least 90% win probability", win_max),
        strat("Board maximum", "Bid the full walk-away limit", float(L["walkAway"])),
    ]
    strategies = [x for x in strategies if x]

    # ---- competitor diagnostics
    def q_of(sorted_arr, p):
        return float(sorted_arr[clamp(int(math.floor(p * (len(sorted_arr) - 1))), 0, len(sorted_arr) - 1)])

    comp_stats = []
    for j, c in enumerate(comps):
        arr = comp_bids[j]
        srt = np.sort(arr)
        wins_vs = int(((arr >= top) & ((rec is None) | (arr >= (rec["bid"] if rec else 0)))).sum())
        st = {"id": c["id"], "name": c["name"], "type": c["type"],
              "p5": q_of(srt, 0.05), "p50": q_of(srt, 0.5), "p95": q_of(srt, 0.95),
              "p025": q_of(srt, 0.025), "p975": q_of(srt, 0.975), "mean": float(arr.mean()),
              "cap": float(caps[j]), "winShareVsRec": wins_vs / N, "ood": [], "observedPctl": None, "observedBid": None}
        for key, (lo_, hi_, nm) in RANGES[c["type"]].items():
            v = c.get(key)
            if v is not None and (v < lo_ or v > hi_):
                st["ood"].append(f"{nm} = {v:g} outside calibrated range {lo_} to {hi_}")
        if c["type"] == "pe":
            entry = V / d["ebitda"]
            if c["exitMultiple"] < 0.6 * entry or c["exitMultiple"] > 1.6 * entry:
                st["ood"].append(f"exit multiple {c['exitMultiple']:g}x far from entry multiple {entry:.1f}x")
        ob = c.get("observedBid")
        if ob is not None and ob > 0:
            st["observedPctl"] = float((arr < ob).sum() / N)
            st["observedBid"] = float(ob)
        comp_stats.append(st)

    # ---- warnings
    warnings = []

    def warn(level, title, detail):
        warnings.append({"level": level, "title": title, "detail": detail})

    for cs in comp_stats:
        if cs["ood"]:
            warn("high", cs["name"] + ": assumptions outside calibrated range",
                 "; ".join(cs["ood"]) + ". Treat outputs involving this bidder as unreliable.")
        if cs["observedPctl"] is not None and (cs["observedPctl"] < 0.025 or cs["observedPctl"] > 0.975):
            warn("high", cs["name"] + ": observed bid outside modelled behaviour",
                 "Observed bid EUR %dm sits at the %.1fth percentile of simulated bids (modelled 95%% band EUR %dm to EUR %dm). "
                 "Recalibrate this profile before relying on the recommendation."
                 % (js_round(cs["observedBid"]), cs["observedPctl"] * 100, js_round(cs["p025"]), js_round(cs["p975"])))
    if rec and rec["pWinSe"] > 0.015:
        warn("medium", "Simulation noise is material",
             "Win probability standard error is %.1f pp. Increase the number of simulated auctions." % (rec["pWinSe"] * 100))
    if rng_out and (rng_out["high"] - rng_out["low"]) / V > 0.08:
        warn("medium", "Objective is flat across a wide range",
             "Bids from EUR %dm to EUR %dm score within tolerance: the recommendation is weakly determined by the inputs."
             % (js_round(rng_out["low"]), js_round(rng_out["high"])))
    if d["valueUncertainty"] > 20:
        warn("medium", "High valuation uncertainty",
             "Standalone value uncertainty of %g%% widens the winner's curse; diligence should narrow it before bidding." % d["valueUncertainty"])
    if proposed and not proposed["feasible"]:
        warn("limit", "Deal team proposal exceeds an approval boundary",
             "Proposed bid EUR %dm: %s." % (js_round(prop), ", ".join(proposed["violations"])))
    if rec:
        p = rec["parts"]
        share = (p["strategic"] + p["emotional"]) / max(1e-9, abs(p["financial"]) + p["strategic"] + p["emotional"])
        if share > 0.4:
            warn("limit", "Recommendation leans on non-financial assumptions",
                 "%d%% of the weighted score at the system bid comes from user-supplied strategic and emotional value, not from measured cash flows." % js_round(share * 100))
        if rec["expFvWin"] < 0:
            warn("limit", "System bid destroys value on average if it wins", "Only the non-financial weights justify it. Check whether that is intended.")
    if adaptation and adaptation["enabled"] and adaptation["status"] == "not":
        warn("high", "Rivals did not settle into an equilibrium",
             "Best responses were still changing after %d rounds. Treat the adaptive result as unreliable; increase the number of simulated auctions." % adaptation["rounds"])
    if L["walkAway"] < 0.85 * V:
        warn("info", "Board maximum is well below standalone value",
             "Walk-away of EUR %dm sits %.0f%% below the standalone value of EUR %dm. "
             "Bids above standalone are often needed to win competitive auctions."
             % (js_round(L["walkAway"]), (1 - L["walkAway"] / V) * 100, js_round(V)))
    if walk_away:
        if curve and all(c["pWin"] < 0.005 for c in curve):
            warn("high", "No bid can win at the current approval boundary",
                 "Every rival outbids your ceiling of EUR %dm in every simulated auction. "
                 "Weights, strategic value and emotional value cannot change this outcome: when no bid wins, no value is created. "
                 "Raise the board walk-away above EUR %dm to compete, or confirm this target is out of reach."
                 % (js_round(ceiling), js_round(float(np.median(top)))))
        elif not feasible:
            warn("info", "No feasible bid under current guardrails",
                 "Every candidate bid breaches at least one approval boundary. Loosen a boundary, or confirm walk-away.")
        else:
            warn("info", "No feasible bid creates value",
                 "The best feasible bid scores at or below zero on the weighted objective. Reduce risk weighting or raise the loss tolerance.")
    if best_all and rec and abs(best_all["bid"] - rec["bid"]) > step * 0.5 and not best_all["feasible"]:
        warn("info", "Hard limits changed the decision",
             "Preferences alone would bid EUR %dm (%s). The engine capped the system bid at EUR %dm."
             % (js_round(best_all["bid"]), ", ".join(best_all["violations"]), js_round(rec["bid"])))
    levels = {w["level"] for w in warnings}
    reliability = "Low" if "high" in levels else ("Medium" if "medium" in levels else "High")

    # ---- histograms of value created when we win
    dists = [x["_dist"] for x in (rec, proposed) if x is not None]
    if dists:
        alld = np.concatenate([x[:20000] for x in dists])
        hlo = math.floor(alld.min() / 25) * 25 if alld.size else -300
        hhi = math.ceil(alld.max() / 25) * 25 if alld.size else 300
    else:
        hlo, hhi = -300, 300
    hlo, hhi = max(hlo, -600), min(hhi, 600)
    bins = clamp(js_round((hhi - hlo) / 25), 8, 40)

    def histogram(dist):
        if dist is None:
            return None
        w_ = (hhi - hlo) / bins
        idx = np.clip(np.floor((dist - hlo) / w_).astype(int), 0, bins - 1)
        counts = np.bincount(idx, minlength=bins)
        tot = max(1, dist.size)
        return [{"from": hlo + i * w_, "to": hlo + (i + 1) * w_, "share": float(counts[i] / tot)} for i in range(bins)]

    def strip(r):
        if r is None:
            return None
        return {k: v for k, v in r.items() if k != "_dist"}

    # ---- the system-generated bid plan
    bid_plan = None
    if rec:
        bid_plan = {
            "action": "submit" if d["format"] == "sealed" else "stay in up to",
            "bid": rec["bid"], "rangeLow": rng_out["low"], "rangeHigh": rng_out["high"],
            "neverAbove": ceiling,
            "neverAboveReason": {
                "floor": f"the value floor (expected value if we win must stay at or above EUR {js_round(L['minNpv'])}m)",
                "tail": f"the tail-loss limit (worst 5% loss no more than EUR {js_round(L['maxTailLoss'])}m)",
                "walk": "the board walk-away maximum",
                "fin": "committed financing",
            }.get(binding["key"], binding["label"]),
            "rivals": "adaptive" if (adaptation and adaptation["enabled"]) else "fixed rules",
        }

    runtime_ms = js_round((time.perf_counter() - t0) * 1000)
    return {
        "ok": True, "version": VERSION, "rivalModel": RIVAL_MODEL["id"], "seed": seed, "runs": N, "runtimeMs": runtime_ms, "format": d["format"],
        "bidPlan": bid_plan,
        "recommendation": strip(rec), "walkAwayRecommended": walk_away, "range": rng_out,
        "unconstrainedBest": strip(best_all), "proposed": strip(proposed),
        "curve": [strip(r) for r in curve], "strategies": [strip(r) for r in strategies],
        "competitors": comp_stats, "warnings": warnings, "reliability": reliability,
        "histRec": histogram(rec["_dist"]) if rec else None,
        "histProposed": histogram(proposed["_dist"]) if proposed else None,
        "breakEven": break_even, "thresholds": thresholds, "adaptation": adaptation,
    }


def stress_variant(scenario: dict) -> dict:
    """Same case with more aggressive rivals: fever +15 pp, premium +5 pp, shading +2 pp."""
    s = copy.deepcopy(scenario)
    for c in s["competitors"]:
        c["feverProb"] = min(100, c["feverProb"] + 15)
        c["feverPremium"] = c["feverPremium"] + 5
        c["shading"] = min(100, c["shading"] + 2)
    return s
