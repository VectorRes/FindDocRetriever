"""The standard ratio formula library (ID-HU-BE-010).

Two tables:
  - LINE_ITEMS: the statement line items formulas take as inputs, each with
    the statement it normally lives in and the labels accepted as that item
    (English and Spanish). A label outside that list still gets used, but the
    mapping is flagged for the analyst's review (see mapping.py).
  - RATIOS: each ratio's inputs and its arithmetic. Formulas use period-end
    balances as reported in the statement (no averaging of opening/closing
    balances), so e.g. ROA = net income / total assets at period end.

Every `compute` is plain arithmetic over already-verified figures; a zero
denominator raises ZeroDivisionError, which the engine reports as
"can't be calculated" rather than a number.
"""
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

Statement = Literal["income_statement", "balance_sheet"]
Category = Literal["liquidity", "solvency", "profitability", "efficiency"]
# "percent": value is a fraction shown x100 (0.25 -> 25%); "times": shown as 1.8x.
Unit = Literal["percent", "times"]


@dataclass(frozen=True)
class LineItem:
    key: str
    name: str
    # Main Spanish name, added to the extraction query so English metric
    # names still find Spanish-language statements.
    name_es: str
    statement: Statement
    # Labels accepted as this line item without review (besides name/name_es).
    synonyms: tuple[str, ...] = ()

    @property
    def query(self) -> str:
        # The first synonyms too ("Revenue / Ingresos / Sales / Net sales"):
        # statements often use one of them, and retrieval only sees this text.
        names = " / ".join(dict.fromkeys((self.name, self.name_es, *self.synonyms[:2])))
        if self.statement == "balance_sheet":
            # Otherwise a cash-flow line with the same name ("Inventory",
            # the quarter's *change* in inventory) can be taken for the balance.
            return f"{names} (balance sheet amount at the period-end date)"
        return names

    @property
    def accepted_labels(self) -> tuple[str, ...]:
        return (self.name, self.name_es, *self.synonyms)


@dataclass(frozen=True)
class RatioDefinition:
    key: str
    name: str
    category: Category
    formula: str
    inputs: tuple[str, ...]
    compute: Callable[[dict[str, float]], float]
    unit: Unit
    description: str
    # (min, max) a correctly-mapped ratio can reasonably take; None = unbounded.
    # A value outside it almost always means inputs from different periods or
    # line items (e.g. nine-month net income over one quarter's revenue), so
    # the engine holds it for review instead of presenting it as a result.
    plausible: tuple[float | None, float | None] = (None, None)


# Margins can't exceed 100% of revenue; the rest are generous sanity bounds.
_PLAUSIBLE: dict[str, tuple[float | None, float | None]] = {
    "current_ratio": (0, 50),
    "quick_ratio": (0, 50),
    "cash_ratio": (0, 50),
    "debt_ratio": (0, 10),
    "gross_margin": (None, 1),
    "operating_margin": (None, 1),
    "ebitda_margin": (None, 1),
    "net_margin": (None, 1),
    "roa": (-1, 1),
    "asset_turnover": (0, 20),
    "inventory_turnover": (0, 365),
    "receivables_turnover": (0, 365),
}


_ITEMS = [
    # Income statement
    LineItem("revenue", "Revenue", "Ingresos", "income_statement", (
        "Sales", "Net sales", "Net revenue", "Total revenue", "Revenues", "Turnover",
        "Ventas", "Ventas netas", "Ingresos operacionales", "Ingresos de actividades ordinarias",
        "Ingresos totales", "Ingresos netos",
    )),
    LineItem("cost_of_sales", "Cost of sales", "Costo de ventas", "income_statement", (
        "Cost of goods sold", "COGS", "Cost of revenue", "Cost of revenues", "Costo de los bienes vendidos",
        "Costos de ventas", "Costo de mercancia vendida",
    )),
    LineItem("gross_profit", "Gross profit", "Utilidad bruta", "income_statement", (
        "Gross margin", "Ganancia bruta", "Margen bruto",
    )),
    LineItem("operating_income", "Operating income", "Utilidad operacional", "income_statement", (
        "Operating profit", "EBIT", "Income from operations", "Utilidad operativa",
        "Resultado operacional", "Ganancia operacional",
    )),
    LineItem("ebitda", "EBITDA", "EBITDA", "income_statement", ("Adjusted EBITDA", "EBITDA ajustado")),
    LineItem("interest_expense", "Interest expense", "Gastos financieros", "income_statement", (
        "Finance costs", "Financial expenses", "Interest", "Gasto por intereses", "Costos financieros", "Intereses",
    )),
    LineItem("net_income", "Net income", "Utilidad neta", "income_statement", (
        "Net profit", "Net earnings", "Profit for the year", "Profit for the period",
        "Ganancia neta", "Utilidad del ejercicio", "Resultado del ejercicio", "Utilidad neta del ejercicio",
        # NIIF (IFRS) wording used in Colombian statements.
        "Ganancia (pérdida) del periodo", "Ganancia o (pérdida) del periodo", "Ganancia (pérdida) del ejercicio",
        "Resultado del periodo", "Utilidad del periodo", "Utilidad (pérdida) del ejercicio",
    )),
    # Balance sheet
    LineItem("cash", "Cash and cash equivalents", "Efectivo y equivalentes de efectivo", "balance_sheet", (
        "Cash", "Efectivo", "Caja y bancos", "Disponible",
    )),
    LineItem("accounts_receivable", "Accounts receivable", "Cuentas por cobrar", "balance_sheet", (
        "Trade receivables", "Trade and other receivables", "Receivables", "Deudores comerciales",
        "Cuentas comerciales por cobrar", "Deudores",
    )),
    LineItem("inventory", "Inventory", "Inventarios", "balance_sheet", ("Inventories", "Inventario")),
    LineItem("current_assets", "Current assets", "Activos corrientes", "balance_sheet", (
        "Total current assets", "Activo corriente", "Total activo corriente", "Total activos corrientes",
    )),
    LineItem("total_assets", "Total assets", "Activos totales", "balance_sheet", (
        "Assets", "Total activos", "Total activo", "Activo total",
    )),
    LineItem("current_liabilities", "Current liabilities", "Pasivos corrientes", "balance_sheet", (
        "Total current liabilities", "Pasivo corriente", "Total pasivo corriente", "Total pasivos corrientes",
    )),
    LineItem("total_liabilities", "Total liabilities", "Pasivos totales", "balance_sheet", (
        "Liabilities", "Total pasivos", "Total pasivo", "Pasivo total",
    )),
    LineItem("total_equity", "Total equity", "Patrimonio", "balance_sheet", (
        "Equity", "Shareholders' equity", "Stockholders' equity", "Total shareholders' equity",
        "Total patrimonio", "Patrimonio total", "Patrimonio neto",
    )),
]
LINE_ITEMS: dict[str, LineItem] = {item.key: item for item in _ITEMS}

# Totals that start with a line item's name but are a different figure: the
# balance sheet's grand total is not total liabilities. Never taken as an input.
COMBINED_TOTALS: tuple[str, ...] = (
    "Total liabilities and equity", "Total liabilities and stockholders' equity",
    "Total liabilities and shareholders' equity", "Total liabilities and net assets",
    "Liabilities and equity", "Liabilities and stockholders' equity", "Liabilities and shareholders' equity",
    "Total pasivos y patrimonio", "Total pasivo y patrimonio", "Total pasivo y patrimonio neto",
    "Pasivos y patrimonio", "Pasivo y patrimonio",
)

# Balances that are never negative in a correctly-read statement. A negative
# one (negative equity, or a sign/line-item mix-up) makes ratios built on it
# misleading, e.g. a negative debt-to-equity, so the engine flags them.
# Profit lines, cost of sales and interest expense can legitimately be negative.
POSITIVE_ITEMS = frozenset({
    "revenue", "cash", "accounts_receivable", "inventory", "current_assets", "total_assets",
    "current_liabilities", "total_liabilities", "total_equity",
})


def _ratio(key, name, category, formula, inputs, compute, unit, description) -> RatioDefinition:
    return RatioDefinition(
        key, name, category, formula, tuple(inputs), compute, unit, description,
        _PLAUSIBLE.get(key, (None, None)),
    )


_RATIOS = [
    # Liquidity
    _ratio("current_ratio", "Current ratio", "liquidity", "Current assets / Current liabilities",
           ["current_assets", "current_liabilities"],
           lambda v: v["current_assets"] / v["current_liabilities"], "times",
           "Ability to cover short-term obligations with short-term assets."),
    _ratio("quick_ratio", "Quick ratio", "liquidity", "(Current assets - Inventory) / Current liabilities",
           ["current_assets", "inventory", "current_liabilities"],
           lambda v: (v["current_assets"] - v["inventory"]) / v["current_liabilities"], "times",
           "Current ratio excluding inventory, the least liquid current asset."),
    _ratio("cash_ratio", "Cash ratio", "liquidity", "Cash and cash equivalents / Current liabilities",
           ["cash", "current_liabilities"],
           lambda v: v["cash"] / v["current_liabilities"], "times",
           "Short-term obligations covered by cash alone."),
    # Solvency
    _ratio("debt_to_equity", "Debt-to-equity", "solvency", "Total liabilities / Total equity",
           ["total_liabilities", "total_equity"],
           lambda v: v["total_liabilities"] / v["total_equity"], "times",
           "Leverage: liabilities per unit of equity (total liabilities, not only financial debt)."),
    _ratio("debt_ratio", "Debt ratio", "solvency", "Total liabilities / Total assets",
           ["total_liabilities", "total_assets"],
           lambda v: v["total_liabilities"] / v["total_assets"], "percent",
           "Share of assets financed with liabilities."),
    _ratio("interest_coverage", "Interest coverage", "solvency", "Operating income / Interest expense",
           ["operating_income", "interest_expense"],
           # Interest expense is often reported as a negative line; coverage uses its magnitude.
           lambda v: v["operating_income"] / abs(v["interest_expense"]), "times",
           "How many times operating income covers interest expense."),
    # Profitability
    _ratio("gross_margin", "Gross margin", "profitability", "Gross profit / Revenue",
           ["gross_profit", "revenue"],
           lambda v: v["gross_profit"] / v["revenue"], "percent",
           "Revenue left after the cost of sales."),
    _ratio("operating_margin", "Operating margin", "profitability", "Operating income / Revenue",
           ["operating_income", "revenue"],
           lambda v: v["operating_income"] / v["revenue"], "percent",
           "Revenue left after operating expenses."),
    _ratio("ebitda_margin", "EBITDA margin", "profitability", "EBITDA / Revenue",
           ["ebitda", "revenue"],
           lambda v: v["ebitda"] / v["revenue"], "percent",
           "Operating cash earnings before depreciation and amortization, per unit of revenue."),
    _ratio("net_margin", "Net profit margin", "profitability", "Net income / Revenue",
           ["net_income", "revenue"],
           lambda v: v["net_income"] / v["revenue"], "percent",
           "Revenue left as profit after all expenses."),
    _ratio("roa", "Return on assets (ROA)", "profitability", "Net income / Total assets",
           ["net_income", "total_assets"],
           lambda v: v["net_income"] / v["total_assets"], "percent",
           "Profit generated per unit of assets (period-end total assets)."),
    _ratio("roe", "Return on equity (ROE)", "profitability", "Net income / Total equity",
           ["net_income", "total_equity"],
           lambda v: v["net_income"] / v["total_equity"], "percent",
           "Profit generated per unit of equity (period-end equity)."),
    # Efficiency
    _ratio("asset_turnover", "Asset turnover", "efficiency", "Revenue / Total assets",
           ["revenue", "total_assets"],
           lambda v: v["revenue"] / v["total_assets"], "times",
           "Revenue generated per unit of assets."),
    _ratio("inventory_turnover", "Inventory turnover", "efficiency", "Cost of sales / Inventory",
           ["cost_of_sales", "inventory"],
           # Cost of sales is often reported as a negative line; turnover uses its magnitude.
           lambda v: abs(v["cost_of_sales"]) / v["inventory"], "times",
           "How many times inventory is sold through in the period."),
    _ratio("receivables_turnover", "Receivables turnover", "efficiency", "Revenue / Accounts receivable",
           ["revenue", "accounts_receivable"],
           lambda v: v["revenue"] / v["accounts_receivable"], "times",
           "How many times receivables are collected in the period."),
]
RATIOS: dict[str, RatioDefinition] = {ratio.key: ratio for ratio in _RATIOS}

CATEGORIES: tuple[Category, ...] = ("liquidity", "solvency", "profitability", "efficiency")
