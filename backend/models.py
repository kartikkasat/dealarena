"""Request and response models for the DealArena API (validated with Pydantic)."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class Deal(BaseModel):
    name: str = Field("Project Atlas (hypothetical)", max_length=120)
    ebitda: float = Field(100, gt=0, description="Target LTM EBITDA, EURm")
    standaloneValue: float = Field(1000, gt=0, description="Standalone enterprise value, EURm")
    valueUncertainty: float = Field(8, ge=0, le=60, description="% standard deviation of the true value")
    format: Literal["sealed", "english"] = "sealed"
    increment: float = Field(1.0, ge=0, le=10, description="% bid increment in an ascending auction")


class Acquirer(BaseModel):
    proposedBid: Optional[float] = Field(1180, ge=0, description="Optional deal-team proposal to compare against, EURm")
    synergies: float = Field(200, ge=0, description="PV of estimated synergies, EURm (user assumption)")
    synergyRealisation: float = Field(75, ge=0, le=130, description="% of synergies expected to be realised")
    synergyRealisationSd: float = Field(20, ge=0, le=60)
    strategicValue: float = Field(60, ge=0, description="User-supplied, not measured, EURm")
    emotionalValue: float = Field(25, ge=0, description="User-supplied, not measured, EURm")


class Limits(BaseModel):
    walkAway: float = Field(1200, gt=0, description="Board-approved maximum bid, EURm")
    financing: float = Field(1350, gt=0, description="Committed financing, EURm")
    requireNonNegativeNpv: bool = True
    minNpv: float = 0
    maxTailLoss: float = Field(200, ge=0, description="Worst-5% average loss tolerated if we win, EURm")


class Weights(BaseModel):
    financial: float = Field(100, ge=0, le=150)
    strategic: float = Field(50, ge=0, le=150)
    emotional: float = Field(20, ge=0, le=150)
    risk: float = Field(50, ge=0, le=150)


class Sim(BaseModel):
    runs: int = Field(10000, ge=500, le=50000, description="Number of simulated auctions")
    seed: int = Field(20261030, ge=0, le=4294967295)
    randomSeed: bool = Field(True, description="Draw a fresh seed for every run (the seed used is returned and logged)")
    gridPoints: int = Field(61, ge=21, le=121)
    adaptive: bool = Field(False, description="Rivals adapt to each other by iterated best response")


class Competitor(BaseModel):
    id: str = Field(..., max_length=40)
    name: str = Field(..., max_length=80)
    type: Literal["strategic", "pe"]
    enabled: bool = True
    signalNoise: float = Field(..., ge=0, le=60)
    shading: float = Field(..., ge=50, le=120)
    feverProb: float = Field(0, ge=0, le=100)
    feverPremium: float = Field(0, ge=0, le=50)
    observedBid: Optional[float] = Field(None, ge=0)
    # strategic buyer
    synergyPct: Optional[float] = Field(None, ge=0, le=100)
    financingLimit: Optional[float] = Field(None, ge=0)
    # private equity sponsor
    targetIrr: Optional[float] = Field(None, ge=0, le=60)
    holdYears: Optional[float] = Field(None, ge=1, le=10)
    ebitdaGrowth: Optional[float] = Field(None, ge=-20, le=50)
    ebitdaUplift: Optional[float] = Field(None, ge=-20, le=100)
    exitMultiple: Optional[float] = Field(None, ge=0, le=40)
    leverage: Optional[float] = Field(None, ge=0, le=12)
    debtPaydown: Optional[float] = Field(None, ge=0, le=100)
    maxEquity: Optional[float] = Field(None, ge=0)

    @model_validator(mode="after")
    def required_by_type(self):
        need = ["synergyPct", "financingLimit"] if self.type == "strategic" else \
            ["targetIrr", "holdYears", "ebitdaGrowth", "ebitdaUplift", "exitMultiple", "leverage", "debtPaydown", "maxEquity"]
        missing = [f for f in need if getattr(self, f) is None]
        if missing:
            raise ValueError(f"{self.name}: missing {', '.join(missing)} for a {self.type} bidder")
        return self


class Scenario(BaseModel):
    deal: Deal = Deal()
    acquirer: Acquirer = Acquirer()
    limits: Limits = Limits()
    weights: Weights = Weights()
    sim: Sim = Sim()
    competitors: list[Competitor] = Field(..., min_length=1, max_length=6)

    @model_validator(mode="after")
    def at_least_one_enabled(self):
        if not any(c.enabled for c in self.competitors):
            raise ValueError("Enable at least one competitor")
        return self


class RunMeta(BaseModel):
    runId: str
    kind: Literal["simulate", "stress", "replay"]
    createdAt: str
    seed: int
    inputHash: str
    durationMs: int
    replayOf: Optional[str] = None
    rivalModel: str = ""


class SimulationResponse(BaseModel):
    run: RunMeta
    result: dict[str, Any]


class RunSummary(BaseModel):
    runId: str
    kind: str
    createdAt: str
    seed: int
    runs: int
    inputHash: str
    durationMs: int
    systemBid: Optional[float]
    pWin: Optional[float]
    maxBid: Optional[float]
    adaptive: bool
    warnings: int
    reliability: str
    replayOf: Optional[str] = None
    changes: str = ""
    rivalModel: str = ""
    decision: Optional[dict[str, Any]] = None


class DecisionIn(BaseModel):
    """Deal committee decision on one logged run. The tool records it; it never submits a bid."""
    approver: str = Field(..., min_length=2, max_length=80, description="Name of the person signing off")
    decision: Literal["approved", "rejected"]
    note: str = Field("", max_length=300)

    @model_validator(mode="after")
    def _strip(self):
        self.approver = self.approver.strip()
        self.note = self.note.strip()
        if len(self.approver) < 2:
            raise ValueError("approver: enter the name of the person signing off")
        return self
