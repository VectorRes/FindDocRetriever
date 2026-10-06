"""Escalation-team suggestions (ID-HU-BE-009, reused by ID-HU-FE-003).

When the system can't answer confidently — or a comparison finds figures that
don't reconcile — it points the analyst at the team that owns the topic
instead of leaving them to guess. This only *suggests* a team; actually
creating and routing an escalation ticket is ID-HU-INT-008.

The topic is normally classified by the QA model (it picks one of `TOPICS`
in its structured output); `classify_topic` is the keyword fallback for when
the model returns "general" or isn't involved at all.
"""
import re
from dataclasses import dataclass

# Topic -> team that owns it. Edit this table to match the organisation.
TOPIC_TEAMS: dict[str, str] = {
    "revenue_recognition": "Accounting/Consolidation",
    "accounting": "Accounting/Consolidation",
    "consolidation": "Accounting/Consolidation",
    "tax": "Tax",
    "treasury": "Treasury",
    "compensation": "HR/Compensation",
    "legal": "Legal",
    "budget_forecast": "FP&A",
    "general": "Financial Reporting",
}
TOPICS = tuple(TOPIC_TEAMS)

# Spanish and English cues, checked in this order (most specific first).
_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("revenue_recognition", (
        "revenue recognition", "recognize revenue", "recognise revenue", "ifrs 15", "asc 606",
        "reconocimiento de ingresos", "reconocer ingresos", "niif 15", "performance obligation",
        "obligación de desempeño", "obligacion de desempeno",
    )),
    ("consolidation", (
        "consolidation", "consolidación", "consolidacion", "intercompany", "intercompañía",
        "intercompania", "eliminations", "eliminaciones", "reconcile", "reconciliation",
        "conciliación", "conciliacion", "concilia",
    )),
    ("tax", (
        "tax", "impuesto", "iva", "vat", "withholding", "retención", "retencion", "deferred tax",
        "dian",
    )),
    ("treasury", (
        "cash flow", "flujo de caja", "liquidity", "liquidez", "exchange rate", "tasa de cambio",
        "fx", "debt", "deuda", "loan", "préstamo", "prestamo", "covenant", "hedge", "cobertura",
    )),
    ("compensation", (
        "compensation", "compensación", "compensacion", "salary", "salario", "payroll", "nómina",
        "nomina", "bonus", "bonificación", "bonificacion",
    )),
    ("legal", (
        "legal", "litigation", "litigio", "lawsuit", "contingenc", "contract dispute",
    )),
    ("budget_forecast", (
        "budget", "presupuesto", "forecast", "pronóstico", "pronostico", "proyección",
        "proyeccion", "plan financiero",
    )),
    ("accounting", (
        "accounting treatment", "tratamiento contable", "provision", "provisión", "depreciation",
        "depreciación", "depreciacion", "amortization", "amortización", "impairment",
        "deterioro", "lease", "arrendamiento", "accrual", "causación", "causacion",
    )),
]


@dataclass
class EscalationSuggestion:
    team: str
    topic: str
    reason: str


def normalize_topic(topic: str | None) -> str:
    value = (topic or "").strip().lower()
    return value if value in TOPIC_TEAMS else "general"


def classify_topic(text: str) -> str:
    """Keyword fallback classification of a question/metric into a topic."""
    lowered = text.lower()
    for topic, cues in _KEYWORDS:
        for cue in cues:
            # Cues match from a word start ("lease" not inside "release");
            # short ones ("fx", "iva", "tax") must be the whole word.
            pattern = rf"\b{re.escape(cue)}\b" if len(cue) <= 4 else rf"\b{re.escape(cue)}"
            if re.search(pattern, lowered):
                return topic
    return "general"


def resolve_topic(model_topic: str | None, text: str) -> str:
    """Prefer the model's classification; fall back to keywords when it's generic."""
    topic = normalize_topic(model_topic)
    return topic if topic != "general" else classify_topic(text)


def suggest_escalation(topic: str, reason: str) -> EscalationSuggestion:
    topic = normalize_topic(topic)
    return EscalationSuggestion(team=TOPIC_TEAMS[topic], topic=topic, reason=reason)
