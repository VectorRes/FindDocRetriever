"""Source-of-truth hierarchy (ID-HU-BE-016).

Every document has a source tier, most authoritative first:

  1. erp_epm          — data exported from the ERP/EPM system (the source of truth);
  2. official_report  — approved / published reports (annual report, 10-K/10-Q,
                        audited statements, earnings releases);
  3. internal_report  — internal management reports, BI extracts, working files.

The tier is inferred from the file name at ingestion (conservatively: anything
not recognisably an ERP/EPM export or an official report is an internal
report) and can be changed by a reviewer. Answers prefer the highest tier
available for a figure and disclose when they had to use a lower one
(app/qa/source_hierarchy.py).
"""
import re

ERP_EPM = "erp_epm"
OFFICIAL_REPORT = "official_report"
INTERNAL_REPORT = "internal_report"

TIERS = (ERP_EPM, OFFICIAL_REPORT, INTERNAL_REPORT)
DEFAULT_TIER = INTERNAL_REPORT
TIER_NAMES = {ERP_EPM: "ERP/EPM", OFFICIAL_REPORT: "official report", INTERNAL_REPORT: "internal report"}


def tier_rank(tier: str | None) -> int:
    """0 for ERP/EPM, the most authoritative; an unknown tier ranks last."""
    return TIERS.index(tier) if tier in TIERS else len(TIERS) - 1


# Whole words of a file name (split on spaces, "_", "-", ".").
_ERP_EPM_WORDS = {
    "erp", "epm", "sap", "s4hana", "hana", "oracle", "netsuite", "hyperion", "onestream", "anaplan",
    "dynamics", "siesa", "jde", "jdedwards", "tagetik", "cpm",
}
_OFFICIAL_PATTERNS = re.compile(
    r"10[-_ ]?[kq]\b|20[-_ ]?f\b|annual[-_ ]report|informe[-_ ]anual|earnings[-_ ]release|as[-_ ]filed"
    r"|audited|auditad[oa]s?|dictaminad[oa]s?|estados[-_ ]financieros[-_ ]auditados|official|oficial"
    r"|press[-_ ]release|supersociedades|\bsec\b",
    re.IGNORECASE,
)


def infer_tier(filename: str) -> str:
    """The tier a file name suggests: ERP/EPM exports by their system's name
    ("SAP_export_Q2_2026.xlsx", "EPM_Andina_2025.xlsx"), official reports by
    their kind ("10-Q", "annual report", "audited"); else an internal report."""
    stem = filename.rsplit(".", 1)[0]
    words = {w.lower() for w in re.split(r"[\s_.\-()]+", stem) if w}
    if words & _ERP_EPM_WORDS:
        return ERP_EPM
    if _OFFICIAL_PATTERNS.search(stem):
        return OFFICIAL_REPORT
    return INTERNAL_REPORT
