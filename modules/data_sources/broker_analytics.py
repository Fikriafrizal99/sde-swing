from __future__ import annotations

"""Broker multi-day classification, persistence, rotation, acceleration,
divergence, alignment, and foreign double-count protection (sections M–R).

Nothing here emits BUY/WATCH/AVOID.  Every function returns scores, context
labels, confidence values, penalties, blockers, and trace strings that the
Decision Engine consumes as opaque context.
"""

from dataclasses import dataclass, field
from typing import Any

from modules.data_sources.broker_windows import WindowFeatures

# ---------------------------------------------------------------------------
# M. Classification
# ---------------------------------------------------------------------------
STRONG_ACCUMULATION = "STRONG_ACCUMULATION"
ACCUMULATION = "ACCUMULATION"
EARLY_ACCUMULATION = "EARLY_ACCUMULATION"
NEUTRAL = "NEUTRAL"
MIXED = "MIXED"
DIVERGENCE = "DIVERGENCE"
DISTRIBUTION = "DISTRIBUTION"
STRONG_DISTRIBUTION = "STRONG_DISTRIBUTION"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

ALL_CLASSIFICATIONS = (
    STRONG_ACCUMULATION, ACCUMULATION, EARLY_ACCUMULATION,
    NEUTRAL, MIXED, DIVERGENCE, DISTRIBUTION, STRONG_DISTRIBUTION,
    INSUFFICIENT_DATA,
)

# Largest-day contribution threshold above which confidence is penalised.
SINGLE_DAY_DOMINATION_THRESHOLD = 0.60


@dataclass
class ClassificationResult:
    classification: str
    confidence: float          # 0–100
    score: float               # signed, positive = accumulation
    penalty: float             # 0–100, applied by Decision Engine
    blocker: bool              # strong distribution is a hard blocker
    trace: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "confidence": round(self.confidence, 2),
            "score": round(self.score, 4),
            "penalty": round(self.penalty, 2),
            "blocker": self.blocker,
            "trace": self.trace,
        }


def classify_window(wf: WindowFeatures) -> ClassificationResult:
    trace: list[str] = []
    if not wf.window_complete or wf.available_sessions < 1:
        return ClassificationResult(INSUFFICIENT_DATA, 0.0, 0.0, 0.0, False,
                                    [f"coverage={wf.coverage_ratio:.2f}"])

    net = wf.cumulative_net_value
    pos_ratio = wf.positive_day_ratio
    consistency = wf.flow_consistency
    persistence = len(wf.persistent_top_buyers) / max(1, len(wf.persistent_top_buyers) + len(wf.persistent_top_sellers))
    concentration = wf.buyer_concentration
    acceleration = wf.flow_acceleration
    largest = wf.largest_day_contribution

    # Base score: normalised cumulative flow direction.
    score = 0.0
    if net > 0:
        score += 30.0 * min(1.0, pos_ratio / 0.7)
        score += 20.0 * consistency
        score += 15.0 * min(1.0, persistence)
        score += 10.0 * min(1.0, concentration)
        score += 10.0 * max(0.0, acceleration)
        trace.append(f"net>0 pos_ratio={pos_ratio:.2f} consistency={consistency:.2f}")
    elif net < 0:
        score -= 30.0 * min(1.0, wf.negative_day_ratio / 0.7)
        score -= 20.0 * consistency
        score -= 15.0 * min(1.0, len(wf.persistent_top_sellers) / max(1, len(wf.persistent_top_sellers)))
        score -= 10.0 * wf.seller_concentration
        score -= 10.0 * max(0.0, -acceleration)
        trace.append(f"net<0 neg_ratio={wf.negative_day_ratio:.2f}")

    # Single-day domination penalty: one big day must not drive strong label.
    penalty = 0.0
    if largest >= SINGLE_DAY_DOMINATION_THRESHOLD and wf.available_sessions >= 2:
        penalty = 20.0
        score *= 0.6
        trace.append(f"single_day_domination largest={largest:.2f} penalty=20")

    # Classify.
    if score >= 60:
        cls = STRONG_ACCUMULATION
    elif score >= 30:
        cls = ACCUMULATION
    elif score >= 10:
        cls = EARLY_ACCUMULATION
    elif score <= -60:
        cls = STRONG_DISTRIBUTION
    elif score <= -30:
        cls = DISTRIBUTION
    elif score <= -10:
        cls = DIVERGENCE
    elif abs(score) < 10 and abs(net) > 0:
        cls = MIXED
    else:
        cls = NEUTRAL

    # Strong distribution is always a hard blocker.
    blocker = cls == STRONG_DISTRIBUTION
    if blocker:
        trace.append("HARD_BLOCKER: STRONG_DISTRIBUTION")

    confidence = min(100.0, max(0.0, 40.0 + abs(score) * 0.6)) * wf.coverage_ratio
    return ClassificationResult(cls, confidence, score, penalty, blocker, trace)


# ---------------------------------------------------------------------------
# N. Persistence and rotation
# ---------------------------------------------------------------------------
PERSISTENT_BUYER = "PERSISTENT_BUYER"
PERSISTENT_SELLER = "PERSISTENT_SELLER"
NEW_ACCUMULATOR = "NEW_ACCUMULATOR"
NEW_DISTRIBUTOR = "NEW_DISTRIBUTOR"
EXITED_DOMINANT_BUYER = "EXITED_DOMINANT_BUYER"
EXITED_DOMINANT_SELLER = "EXITED_DOMINANT_SELLER"
HEALTHY_ROTATION = "HEALTHY_ROTATION"
HIGH_ROTATION = "HIGH_ROTATION"
STABLE_DOMINANCE = "STABLE_DOMINANCE"


@dataclass
class PersistenceResult:
    buyer_overlap_ratio: float
    seller_overlap_ratio: float
    buyer_rotation_status: str
    seller_rotation_status: str
    new_buyer_count: int
    exited_buyer_count: int
    new_seller_count: int
    exited_seller_count: int
    persistent_buyers: list[str]
    persistent_sellers: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "Buyer_Overlap_Ratio": round(self.buyer_overlap_ratio, 4),
            "Seller_Overlap_Ratio": round(self.seller_overlap_ratio, 4),
            "Buyer_Rotation_Status": self.buyer_rotation_status,
            "Seller_Rotation_Status": self.seller_rotation_status,
            "New_Buyer_Count": self.new_buyer_count,
            "Exited_Buyer_Count": self.exited_buyer_count,
            "New_Seller_Count": self.new_seller_count,
            "Exited_Seller_Count": self.exited_seller_count,
            "Persistent_Buyers": self.persistent_buyers,
            "Persistent_Sellers": self.persistent_sellers,
        }


def _rotation_status(overlap: float, rotation: float) -> str:
    if overlap >= 0.7:
        return STABLE_DOMINANCE
    if rotation >= 0.7:
        return HIGH_ROTATION
    return HEALTHY_ROTATION


def compute_persistence(
    earlier: WindowFeatures,
    later: WindowFeatures,
) -> PersistenceResult:
    e_buyers = set(earlier.persistent_top_buyers)
    l_buyers = set(later.persistent_top_buyers)
    e_sellers = set(earlier.persistent_top_sellers)
    l_sellers = set(later.persistent_top_sellers)

    buyer_overlap = len(e_buyers & l_buyers) / max(1, len(e_buyers | l_buyers))
    seller_overlap = len(e_sellers & l_sellers) / max(1, len(e_sellers | l_sellers))

    return PersistenceResult(
        buyer_overlap_ratio=round(buyer_overlap, 4),
        seller_overlap_ratio=round(seller_overlap, 4),
        buyer_rotation_status=_rotation_status(buyer_overlap, later.buyer_rotation),
        seller_rotation_status=_rotation_status(seller_overlap, later.seller_rotation),
        new_buyer_count=len(l_buyers - e_buyers),
        exited_buyer_count=len(e_buyers - l_buyers),
        new_seller_count=len(l_sellers - e_sellers),
        exited_seller_count=len(e_sellers - l_sellers),
        persistent_buyers=sorted(e_buyers & l_buyers),
        persistent_sellers=sorted(e_sellers & l_sellers),
    )


# ---------------------------------------------------------------------------
# O. Flow acceleration and reversal
# ---------------------------------------------------------------------------
ACCELERATING = "ACCELERATING"
STABLE = "STABLE"
DECELERATING = "DECELERATING"
REVERSING = "REVERSING"


def classify_acceleration(acceleration: float) -> str:
    if acceleration >= 0.3:
        return ACCELERATING
    if acceleration <= -0.3:
        return REVERSING
    if acceleration >= 0.1:
        return STABLE
    if acceleration <= -0.1:
        return DECELERATING
    return STABLE


@dataclass
class AccelerationResult:
    acceleration_1d_vs_3d: str | None
    acceleration_3d_vs_5d: str | None
    acceleration_5d_vs_10d: str | None
    acceleration_10d_vs_20d: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "Acceleration_1D_vs_3D": self.acceleration_1d_vs_3d,
            "Acceleration_3D_vs_5D": self.acceleration_3d_vs_5d,
            "Acceleration_5D_vs_10D": self.acceleration_5d_vs_10d,
            "Acceleration_10D_vs_20D": self.acceleration_10d_vs_20d,
        }


def compute_acceleration_across_windows(
    windows: dict[str, WindowFeatures],
) -> AccelerationResult:
    def _compare(short: str, long: str) -> str | None:
        s = windows.get(short)
        l = windows.get(long)
        if s is None or l is None:
            return None
        if not s.window_complete or not l.window_complete:
            return None
        diff = s.average_daily_net_value - l.average_daily_net_value
        scale = max(abs(s.average_daily_net_value), abs(l.average_daily_net_value), 1.0)
        return classify_acceleration(diff / scale)

    return AccelerationResult(
        acceleration_1d_vs_3d=_compare("1D", "3D"),
        acceleration_3d_vs_5d=_compare("3D", "5D"),
        acceleration_5d_vs_10d=_compare("5D", "10D"),
        acceleration_10d_vs_20d=_compare("10D", "20D"),
    )


# ---------------------------------------------------------------------------
# P. Broker-price divergence
# ---------------------------------------------------------------------------
CONFIRMED_ACCUMULATION = "CONFIRMED_ACCUMULATION"
ABSORPTION_CANDIDATE = "ABSORPTION_CANDIDATE"
POSSIBLE_ABSORPTION = "POSSIBLE_ABSORPTION"
NEGATIVE_DIVERGENCE = "NEGATIVE_DIVERGENCE"
DISTRIBUTION_DIVERGENCE = "DISTRIBUTION_DIVERGENCE"
CONFIRMED_DISTRIBUTION = "CONFIRMED_DISTRIBUTION"


@dataclass
class DivergenceResult:
    label: str
    broker_flow_direction: str   # POSITIVE / NEGATIVE / NEUTRAL
    price_return_direction: str  # UP / DOWN / FLAT
    trace: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "Divergence_Label": self.label,
            "Broker_Flow_Direction": self.broker_flow_direction,
            "Price_Return_Direction": self.price_return_direction,
            "Divergence_Trace": self.trace,
        }


def compute_divergence(
    wf: WindowFeatures,
    price_return_pct: float | None,
) -> DivergenceResult:
    if price_return_pct is None or not wf.window_complete:
        return DivergenceResult(INSUFFICIENT_DATA, "UNKNOWN", "UNKNOWN", "no_data")

    flow_dir = "POSITIVE" if wf.cumulative_net_value > 0 else "NEGATIVE" if wf.cumulative_net_value < 0 else "NEUTRAL"
    price_dir = "UP" if price_return_pct > 0.5 else "DOWN" if price_return_pct < -0.5 else "FLAT"

    if flow_dir == "POSITIVE" and price_dir == "UP":
        label = CONFIRMED_ACCUMULATION
    elif flow_dir == "POSITIVE" and price_dir == "DOWN":
        label = ABSORPTION_CANDIDATE
    elif flow_dir == "POSITIVE" and price_dir == "FLAT":
        label = POSSIBLE_ABSORPTION
    elif flow_dir == "NEGATIVE" and price_dir == "UP":
        label = NEGATIVE_DIVERGENCE
    elif flow_dir == "NEGATIVE" and price_dir == "DOWN":
        label = CONFIRMED_DISTRIBUTION
    elif flow_dir == "NEGATIVE" and price_dir == "FLAT":
        label = DISTRIBUTION_DIVERGENCE
    else:
        label = NEUTRAL

    trace = f"flow={flow_dir} price={price_dir} ret={price_return_pct:.2f}%"
    return DivergenceResult(label, flow_dir, price_dir, trace)


# ---------------------------------------------------------------------------
# Q. Daily and multi-day alignment
# ---------------------------------------------------------------------------
FULLY_ALIGNED = "FULLY_ALIGNED"
SHORT_TERM_CONFIRMING = "SHORT_TERM_CONFIRMING"
LONG_TERM_CONFIRMING = "LONG_TERM_CONFIRMING"
SHORT_TERM_REVERSAL = "SHORT_TERM_REVERSAL"
LONG_TERM_DIVERGENCE = "LONG_TERM_DIVERGENCE"
MIXED_ALIGNMENT = "MIXED"


@dataclass
class AlignmentResult:
    context_1d: str
    context_3d: str
    context_5d: str
    context_10d: str
    context_20d: str
    context_primary: str
    context_confidence: float
    alignment: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "Broker_Context_1D": self.context_1d,
            "Broker_Context_3D": self.context_3d,
            "Broker_Context_5D": self.context_5d,
            "Broker_Context_10D": self.context_10d,
            "Broker_Context_20D": self.context_20d,
            "Broker_Context_Primary": self.context_primary,
            "Broker_Context_Confidence": round(self.context_confidence, 2),
            "Broker_Context_Alignment": self.alignment,
        }


_ACCUM_SET = {STRONG_ACCUMULATION, ACCUMULATION, EARLY_ACCUMULATION}
_DISTRIB_SET = {STRONG_DISTRIBUTION, DISTRIBUTION}


def compute_alignment(
    classifications: dict[str, ClassificationResult],
    primary_window: str = "5D",
) -> AlignmentResult:
    def _ctx(w: str) -> str:
        r = classifications.get(w)
        return r.classification if r else INSUFFICIENT_DATA

    c1 = _ctx("1D")
    c3 = _ctx("3D")
    c5 = _ctx("5D")
    c10 = _ctx("10D")
    c20 = _ctx("20D")
    primary = _ctx(primary_window)

    short_accum = c1 in _ACCUM_SET or c3 in _ACCUM_SET
    long_accum = c10 in _ACCUM_SET or c20 in _ACCUM_SET
    short_distrib = c1 in _DISTRIB_SET or c3 in _DISTRIB_SET
    long_distrib = c10 in _DISTRIB_SET or c20 in _DISTRIB_SET

    all_accum = all(
        _ctx(w) in _ACCUM_SET
        for w in ("1D", "3D", "5D", "10D", "20D")
        if classifications.get(w) and classifications[w].classification != INSUFFICIENT_DATA
    )
    if all_accum and len(classifications) >= 3:
        alignment = FULLY_ALIGNED
    elif short_accum and long_accum:
        alignment = FULLY_ALIGNED
    elif short_accum and not long_distrib:
        alignment = SHORT_TERM_CONFIRMING
    elif long_accum and not short_distrib:
        alignment = LONG_TERM_CONFIRMING
    elif short_distrib and not long_distrib:
        alignment = SHORT_TERM_REVERSAL
    elif long_distrib and not short_distrib:
        alignment = LONG_TERM_DIVERGENCE
    else:
        alignment = MIXED_ALIGNMENT

    primary_result = classifications.get(primary_window)
    confidence = primary_result.confidence if primary_result else 0.0

    return AlignmentResult(c1, c3, c5, c10, c20, primary, confidence, alignment)


# ---------------------------------------------------------------------------
# R. Foreign / domestic double-count protection
# ---------------------------------------------------------------------------
@dataclass
class ForeignProtectionResult:
    domestic_net_value: float
    foreign_net_value: float
    aggregate_net_value: float
    double_count_risk: bool
    provenance: str
    trace: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "Domestic_Net_Value": self.domestic_net_value,
            "Foreign_Net_Value": self.foreign_net_value,
            "Aggregate_Net_Value": self.aggregate_net_value,
            "Double_Count_Risk": self.double_count_risk,
            "Foreign_Provenance": self.provenance,
            "Foreign_Protection_Trace": self.trace,
        }


def check_foreign_double_count(
    broker_flow_foreign_net: float,
    aggregate_foreign_net: float | None,
    flow_origin: str,
) -> ForeignProtectionResult:
    """Detect if foreign broker rows and an aggregate foreign feed represent
    the same transactions.

    ``flow_origin`` == "DERIVED_FROM_BROKER" means the foreign net was derived
    from ASING broker rows — it must NOT be summed with an aggregate foreign
    feed that already includes those same transactions.
    """
    risk = False
    trace = ""
    if aggregate_foreign_net is not None and flow_origin == "DERIVED_FROM_BROKER":
        risk = True
        trace = (
            f"DOUBLE_COUNT_RISK: broker_foreign={broker_flow_foreign_net:.0f} "
            f"aggregate_foreign={aggregate_foreign_net:.0f} "
            f"origin={flow_origin} — use only one source"
        )
    else:
        trace = f"origin={flow_origin} no_double_count"

    # Use aggregate when available and not at risk; otherwise use broker-derived.
    if aggregate_foreign_net is not None and not risk:
        foreign = aggregate_foreign_net
        domestic = 0.0  # not available from aggregate feed alone
    else:
        foreign = broker_flow_foreign_net
        domestic = 0.0

    return ForeignProtectionResult(
        domestic_net_value=domestic,
        foreign_net_value=foreign,
        aggregate_net_value=foreign,
        double_count_risk=risk,
        provenance=flow_origin,
        trace=trace,
    )