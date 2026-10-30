# DealArena: interface decisions

**Summary view is the default.** A committee reader needs the answer, not the charts: the recommended bid and its ceiling, a plain-English sentence on why, three figures (win chance, value if we win, worst 5%), the deal team proposal compared, and any high-severity flag. Everything else (decision-limit chart, rival charts, strategies, risk, run log, the two secondary figures) sits one click away under **Analysis**.

**Decision note.** "Download note (.md)" or "Copy" produces a one-page memo from the current run: recommendation, key figures, proposal comparison, hard limits with the binding one marked, flags, basis, audit trail (run id, seed, engine and rival-model version) and a signature line. It is generated in the browser from the run already on screen; nothing new is computed or sent anywhere. Approval is still recorded in the Run log tab.

**Stress test (kept, demoted).** It reruns the same case with more aggressive rivals (auction-fever probability +15 pp, fever premium +5 pp, bid shading +2 pp) and logs it as a separate "stress" run. It never sets the recommendation, cannot be approved, and returns only a bid and win chance. It is not part of the deck argument, so it moved from the main button bar to the Simulation group as "Run stress test".

**Out-of-date results.** If an input is rejected (for example value uncertainty above 60), the previous result is greyed out and a banner says it is from the previous valid run, instead of showing stale numbers as if they were current.

**Visual language.** System font stack, one accent colour, large rounded white cards on a light grey ground, segmented controls for Summary/Analysis and the Analysis tabs, quiet warnings. No change to the engine, API or any number.

**Not changed.** Engine, API, validation ranges, logging, tests. Mobile table layouts were left as they are (the tool is built for a projector).
