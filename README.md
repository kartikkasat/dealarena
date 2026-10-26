# DealArena: M&A bid decision support (proof of concept)

A runnable prototype for the emlyon Innovation Seminar case "MARL for M&A bidding optimisation".

**What it is.** A rule-based Monte Carlo auction simulator with a Python backend. Rival bidders (strategic buyers and private equity sponsors) each see a noisy estimate of the same target value and bid by configurable rules. For every run the backend draws fresh simulated auctions, generates the bid itself, removes bids that breach hard financial limits, ranks the rest by user-set preference weights, and logs the run.

**What it is not.** It is not a trained multi-agent reinforcement learning (MARL) model, it does not compute an exact Nash equilibrium, and it does not bid on anyone's behalf. All figures in the sample case are hypothetical. In production, the rule-based rivals would be replaced or complemented by trained and validated agents (see the presentation).

No external AI model, paid API or cloud service is used. After the one-time package install, everything runs offline on the presenting computer.

## Run it

Requirements: Python 3.10 or later and a modern browser.

```bash
cd dealarena
python3 -m pip install -r backend/requirements.txt   # once
python3 serve.py
```

Then open:

- On the presenting computer: http://localhost:8007
- On a phone or tablet on the same Wi-Fi: the `http://<your-ip>:8007` address printed by the script
- Interactive API documentation: http://localhost:8007/docs

Options: `python3 serve.py --port 9000` changes the port; `python3 serve.py --local` refuses connections from other devices.

Windows: use `py` instead of `python3`. If the phone cannot connect, allow Python through the firewall for private networks and check that both devices are on the same network (guest and eduroam networks often isolate devices; a phone hotspot works).

The interface needs the backend: opening `index.html` directly from the file system shows "Engine offline".

## How a run works

1. The interface sends the scenario (deal, hard limits, preferences, rivals, simulation settings) to `POST /api/simulate`.
2. The backend validates it (readable error messages for out-of-range values), draws a fresh seed unless a fixed seed is chosen, simulates the auctions and computes the bid.
3. The response contains the **system bid** (what to submit, or the level to stay in up to in an ascending auction), the range within simulation noise, the **maximum** bid allowed by the tightest hard limit, the risk figures, warnings and the run metadata (run id, seed, input hash, duration).
4. The run is written to the run log. Any run can be replayed from its stored inputs and seed; the replay reports whether the result is identical.

The deal team's proposal is optional. When entered under "Compare a proposal", it is evaluated against the same simulated auctions and shown next to the system bid; it never sets the system bid.

## Using it in the presentation

1. The sample case ("Project Atlas", hypothetical) loads and runs automatically.
2. The top of the page shows the system bid, its range, the maximum and the limit that sets it, and five figures: win chance, value if we win, expected value, overpay chance and worst 5% outcome. Below: the decision-limits chart (one bar per hard limit; the shortest bar sets the maximum; the simple break-even is shown dashed for comparison), the synergies needed to break even, and the winner's curse discount.
3. Tabs: **Rivals** (where rival bids land; adaptation before and after), **Strategies** (candidate strategies compared), **Risk** (charts), **Run log** (server log with Replay, JSON and CSV export, and the committee decision on the current run: approver name, optional note, Approve bid or Reject; one decision per run, which cannot be changed).
4. Change any input. With "Rerun when inputs change" on (Simulation group), results update about half a second later; otherwise press **Run simulation**. Each run shows its run id, seed and the change in the system bid.
5. Suggested live sequence (about three minutes):
   - **Preferences:** raise strategic and emotional weights to 100%, then lower risk aversion to 0%. The system bid moves up and a note says it leans on non-financial assumptions.
   - **Hard limits:** lower the board walk-away to EUR 1,020m. The bid is capped and the note "Hard limits changed the decision" explains why.
   - **Rivals:** open Strategic A and enter an observed bid of EUR 1,400m. A warning flags that the rival behaves outside the modelled range and reliability drops to Low.
   - **Rivals adapt to each other** (Rivals group): each rival picks its own most profitable bid level given the others, and we re-optimise, until no one changes. With the fixed sample seed our bid moves from EUR 1,000m to EUR 981m because rivals that stop overpaying bid lower.
   - **Stress:** reruns with more aggressive rivals (auction-fever probability +15 pp, fever premium +5 pp, bid shading +2 pp).
6. **Committee decision:** in the Run log tab, enter an approver name and approve or reject the current run. The decision is logged with the bid, maximum, seed, input hash and model versions (slide 20: the AI advises, a named committee decides).
7. **Model card** (link under the system bid, or `/api/model`): what the rival model is today and how it would become trained agents, stage by stage (slide 11).
8. **Reset** restores the sample case.

**Fresh seeds and the deck figures.** By default every run uses a new random seed, so results vary slightly between runs (in the sample case, roughly EUR 990m to EUR 1,010m with fixed-rule rivals). To reproduce the figures quoted in the presentation, switch off "Fresh random seed each run" in the Simulation group and use seed 20261030.

## API

| Method and path | Purpose |
|---|---|
| `GET /api/health` | Engine status and versions |
| `GET /api/model` | Model card of the rival model: rule-based today, not trained; data, calibration, training, validation and serving status |
| `GET /api/scenario/default` | The sample scenario |
| `POST /api/simulate` | Run a scenario; returns `{run, result}` |
| `POST /api/simulate/stress` | Same scenario with more aggressive rivals |
| `GET /api/runs?limit=50` | Most recent runs (summaries) |
| `GET /api/runs/{run_id}` | Stored inputs and full result of one run |
| `POST /api/runs/{run_id}/replay` | Rerun with the stored inputs and seed; reports `identical` and `inputHashMatches` |
| `POST /api/runs/{run_id}/decision` | Record the committee decision: `{"approver": "...", "decision": "approved" or "rejected", "note": "..."}`. One per run; 409 if already decided, for a stress run, or when approving a walk-away |
| `GET /api/runs.csv` | Run log as CSV |

Invalid input returns HTTP 422 with a list of readable messages in `detail`, for example `sim.runs: Input should be greater than or equal to 500`.

Example:

```bash
curl -s localhost:8007/api/scenario/default > s.json
curl -s -X POST localhost:8007/api/simulate -H 'Content-Type: application/json' -d @s.json
```

## Logs

Created at runtime in `logs/` (not served over HTTP):

- `logs/dealarena.log`: structured JSON application log (requests, runs, errors), rotated at 2 MB (three old files kept).
- `logs/runs.jsonl`: one line per run (run id, kind, time, seed, input hash, system bid, win chance, maximum, binding limit, reliability, duration, which inputs changed since the previous run).
- `logs/runs/<run id>.json`: full inputs and result of each run, used for replay.
- `logs/decisions.jsonl`: committee decisions (run id, approver, approved or rejected, note, time, bid, maximum, seed, input hash, engine version, rival model id).

Delete the `logs/` folder to start with an empty log.

## How the engine works

All values are in EUR millions.

| Step | Rule |
|---|---|
| True value | Standalone value times a lognormal shock with the chosen uncertainty |
| Rival signal | Each rival sees the true value times its own lognormal noise (common-value element, which creates the winner's curse) |
| Strategic rival | Willingness to pay = signal x (1 + its synergies) |
| PE rival | Highest price at which equity (price minus debt) reaches its target IRR at exit: exit EV from EBITDA improvement, growth and exit multiple, debt partly repaid |
| Adaptive rivals (optional) | Sealed bid only: each rival in turn picks the shading on a 2 pp grid (60-100%) that maximises its expected profit given the other rivals' bids and our current bid; we then re-optimise. Repeats until no one changes, or reports settling to within one candidate bid. Iterated best response in a restricted strategy space, not a trained MARL model |
| Rival bid | Sealed bid: willingness x shading; ascending: willingness. With a set probability, "auction fever" adds a premium. Always capped by financing |
| Our outcome if we win | True value + realised synergies - price paid. Synergy realisation is drawn around the expected rate |
| Score | financial weight x expected value + win chance x (strategic weight x strategic value + emotional weight x emotional value) - risk weight x win chance x tail loss |
| Hard limits | Applied before scoring: above walk-away, above financing, expected value if we win below the floor, worst-5% average loss above tolerance |
| Maximum | For each hard limit, the highest bid it allows (bisection on the same simulated auctions); the lowest of these. In an ascending auction the value and tail limits rarely bind, because the winner pays just above the last rival |
| System bid | Highest-scoring feasible bid. The range covers feasible bids within simulation noise of the best score. If no feasible bid scores above zero, the system recommends walking away |

Reliability checks: rival parameters outside the calibrated ranges, observed bids outside the 95% band of simulated bids, simulation noise, a flat objective, high value uncertainty. Reliability describes the model's confidence, not the quality of the deal.

Reproducibility: the same inputs and seed always give the same result. The random generator (Mulberry32 with Box-Muller normals) is the same one used by the earlier browser version, so seed 20261030 reproduces the figures in the deck.

Measured speed on a 2-vCPU server: about 0.2-0.3 s per run with 10,000 simulated auctions, including the decision-limit search.

## Project structure

```
dealarena/
  serve.py                  starts the backend and interface on port 8007
  index.html                page and Vue templates
  css/app.css               styles (responsive down to 360 px)
  js/app.js                 interface (Vue 3, no build step); all computation is on the server
  backend/
    app.py                  FastAPI application and endpoints
    engine.py               simulation engine (NumPy)
    models.py               input validation and response models (Pydantic)
    runlog.py               application log and run log
    requirements.txt        runtime packages
    requirements-dev.txt    test packages
    tests/                  engine and API checks (pytest)
  vendor/                   Vue 3.5 and Chart.js 4.6, bundled for offline use (MIT licences)
  fonts/                    IBM Plex Sans and Sans Condensed (SIL Open Font Licence)
  logs/                     created at runtime
```

Run the checks (38 tests):

```bash
python3 -m pip install -r backend/requirements-dev.txt
python3 -m pytest backend/tests
```

## Troubleshooting

- **"Engine offline" in the top bar.** The page was opened without the backend, or the server stopped. Start `python3 serve.py` and reload.
- **`ModuleNotFoundError: fastapi`** (or uvicorn, numpy). Run `python3 -m pip install -r backend/requirements.txt`. On some Linux distributions add `--user`, or use a virtual environment.
- **Port already in use.** Another process uses 8007; stop it or run `python3 serve.py --port 8010`.
- **Results differ from the deck.** Fresh seeds are on by default; see "Fresh seeds and the deck figures" above.
- **Inputs rerun by themselves.** Switch off "Rerun when inputs change" in the Simulation group.

## Limits of the prototype

- Rival behaviour is rule-based and the parameters are illustrative, not calibrated on transaction data. Adaptive rivals only choose how far to shade their bids; they know our bid and share our model of value uncertainty.
- No trained agents and no deal data: the data, calibration and training stages of the AI pipeline (slide 11) are planned, not built; the model card says which stage is in place.
- The approver name is typed, not authenticated; production needs single sign-on and an approval workflow.
- One-shot auction. No multi-round signalling, exclusivity, deal protection, or bidder entry decisions.
- Losing the auction is valued at zero; the cost of the alternative strategy is not modelled.
- Strategic and emotional values are user estimates. The tool reports their contribution; it cannot verify them.
- The server has no authentication and is meant for a local demonstration, not for confidential deal data.
- Not validated for use on live transactions.
# dealarena
