"""Demo data for the sprint review: ratios dashboard (FE-004) and access audit (BE-014).

Generates the demo workbooks, uploads them to the API and classifies them:

- Andina S.A.S., 2023-2025: income statement + balance sheet per year
  (`internal`). ROE goes 12.0% -> 13.0% -> 34.0% in 2025 because equity fell
  (leverage), so the dashboard flags 2025 as an outlier and names the equity
  multiplier as the driver.
- Pacifico S.A., 2024: income statement only (`internal`). Its liquidity and
  solvency ratios show "insufficient data: the balance sheet for 2024 hasn't
  been ingested".
- Resultados_Q3_2026_PreRelease.xlsx (`restricted:insider`) and
  Compensacion_Ejecutiva_2025.xlsx (`restricted:compensation`): questions
  about them are denied to an analyst and show up in the Audit tab.

Usage (with `docker compose up` running):

    docker compose exec api python scripts/seed_demo.py               # upload
    docker compose exec api python scripts/seed_demo.py --refresh     # + calculate the dashboard
    docker compose exec api python scripts/seed_demo.py --files-only /tmp/demo   # just write the .xlsx

Only the standard library and openpyxl; the API URL defaults to
http://localhost:8000 (override with --api).
"""
import argparse
import json
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import openpyxl

INCOME = {
    # year: (revenue, cost of sales, operating income, EBITDA, interest expense, net income)
    2023: (5_000_000, 3_000_000, 1_150_000, 1_400_000, 180_000, 600_000),
    2024: (5_400_000, 3_200_000, 1_240_000, 1_500_000, 190_000, 650_000),
    2025: (5_800_000, 3_450_000, 1_330_000, 1_600_000, 260_000, 700_400),
}
BALANCE = {
    # year: (cash, receivables, inventory, current assets, total assets, current liabilities,
    #        total liabilities, total equity)
    2023: (900_000, 1_100_000, 700_000, 3_000_000, 8_000_000, 1_500_000, 3_000_000, 5_000_000),
    2024: (950_000, 1_150_000, 720_000, 3_100_000, 8_300_000, 1_550_000, 3_300_000, 5_000_000),
    # Equity cut by a large dividend and buy-back, financed with debt: ROE jumps.
    2025: (800_000, 1_200_000, 760_000, 3_050_000, 8_600_000, 2_100_000, 6_540_000, 2_060_000),
}
PACIFICO_INCOME_2024 = (3_200_000, 2_100_000, 610_000, 780_000, 90_000, 380_000)

DOCUMENTS: list[tuple[str, str]] = []  # (filename, confidentiality tag), filled by build()


def _sheet(wb, title: str, heading: str, rows: list[tuple[str, int]], year_label: str) -> None:
    ws = wb.active if wb.active.title == "Sheet" else wb.create_sheet()
    ws.title = title
    ws.append([heading])
    ws.append(["Cifras en pesos colombianos (COP)"])
    ws.append(["Concepto", year_label])
    for label, value in rows:
        ws.append([label, value])
    ws.column_dimensions["A"].width = 38
    ws.column_dimensions["B"].width = 16


def income_rows(values) -> list[tuple[str, int]]:
    revenue, cost, operating, ebitda, interest, net = values
    return [
        ("Ingresos", revenue), ("Costo de ventas", cost), ("Utilidad bruta", revenue - cost),
        ("Utilidad operacional", operating), ("EBITDA", ebitda), ("Gastos financieros", interest),
        ("Utilidad neta", net),
    ]


def balance_rows(values) -> list[tuple[str, int]]:
    cash, receivables, inventory, current, total, current_liabilities, liabilities, equity = values
    return [
        ("Efectivo y equivalentes de efectivo", cash), ("Cuentas por cobrar", receivables),
        ("Inventarios", inventory), ("Total activo corriente", current), ("Total activos", total),
        ("Total pasivo corriente", current_liabilities), ("Total pasivos", liabilities),
        ("Total patrimonio", equity),
    ]


def build(directory: Path) -> list[tuple[Path, str]]:
    directory.mkdir(parents=True, exist_ok=True)
    files: list[tuple[Path, str]] = []

    def save(wb, name: str, tag: str) -> None:
        path = directory / name
        wb.save(path)
        files.append((path, tag))

    for year in sorted(INCOME):
        wb = openpyxl.Workbook()
        _sheet(wb, "Estado de resultados", f"Andina S.A.S. — Estado de resultados — Año {year}",
               income_rows(INCOME[year]), str(year))
        save(wb, f"EERR_Andina_{year}.xlsx", "internal")
        wb = openpyxl.Workbook()
        _sheet(wb, "Balance general", f"Andina S.A.S. — Estado de situación financiera — Al 31 de diciembre de {year}",
               balance_rows(BALANCE[year]), str(year))
        save(wb, f"Balance_Andina_{year}.xlsx", "internal")

    wb = openpyxl.Workbook()
    _sheet(wb, "Estado de resultados", "Pacifico S.A. — Estado de resultados — Año 2024",
           income_rows(PACIFICO_INCOME_2024), "2024")
    save(wb, "EERR_Pacifico_2024.xlsx", "internal")

    wb = openpyxl.Workbook()
    _sheet(wb, "Resultados Q3", "Andina S.A.S. — Resultados preliminares del tercer trimestre (NO PUBLICADOS)",
           [("Ingresos", 1_620_000), ("Utilidad neta", 205_000)], "Q3 2026")
    save(wb, "Resultados_Q3_2026_PreRelease.xlsx", "restricted:insider")

    wb = openpyxl.Workbook()
    _sheet(wb, "Compensacion", "Compensación ejecutiva 2025",
           [("Salario CEO", 980_000), ("Bono ejecutivo", 450_000), ("Opciones sobre acciones", 300_000)], "2025")
    save(wb, "Compensacion_Ejecutiva_2025.xlsx", "restricted:compensation")
    return files


def _request(method: str, url: str, body: bytes | None = None, headers: dict | None = None, timeout: int = 600):
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read() or b"null")


def upload(api: str, path: Path) -> dict:
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
        "Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n"
    ).encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    return _request("POST", f"{api}/ingest", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--refresh", action="store_true", help="also calculate the dashboard for Andina and Pacifico")
    parser.add_argument("--files-only", metavar="DIR", help="only write the workbooks to DIR")
    args = parser.parse_args()

    if args.files_only:
        for path, tag in build(Path(args.files_only)):
            print(f"{path}  ->  classify as {tag}")
        return 0

    existing = {d["filename"] for d in _request("GET", f"{args.api}/documents")["documents"]}
    with tempfile.TemporaryDirectory() as tmp:
        for path, tag in build(Path(tmp)):
            if path.name in existing:
                print(f"skip    {path.name} (already uploaded)")
                continue
            document = upload(args.api, path)
            if document.get("status") != "ready":
                print(f"FAILED  {path.name}: {document.get('error_message')}")
                continue
            _request(
                "PATCH", f"{args.api}/documents/{document['id']}/confidentiality",
                json.dumps({"tag": tag}).encode(), {"Content-Type": "application/json"},
            )
            print(f"ok      {path.name}  [{tag}]")

    if args.refresh:
        headers = {"Content-Type": "application/json", "X-User-Role": "analyst", "X-User-Id": "demo-seed"}
        for entity in ("Andina", "Pacifico"):
            print(f"calculating the dashboard for {entity} (about a minute)...")
            try:
                dashboard = _request("POST", f"{args.api}/dashboard/refresh",
                                     json.dumps({"entity": entity}).encode(), headers)
            except urllib.error.HTTPError as exc:
                print(f"FAILED  {entity}: {exc.read().decode(errors='replace')}")
                continue
            print(f"ok      {entity}: periods {', '.join(p['period'] for p in dashboard['periods'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
