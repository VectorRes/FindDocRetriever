"""Builds minimal, valid single/multi-page PDFs by hand for tests, so the test
suite doesn't need a PDF-authoring dependency (reportlab/fpdf) on top of the
pypdf reader the app itself uses."""
from __future__ import annotations

import io


def make_pdf_bytes(pages_text: list[str]) -> bytes:
    """pages_text: one string per page (rendered as a single Tj text-show
    operator, so keep it free of unbalanced parentheses/backslashes)."""
    objects: list[tuple[int, str]] = []
    font_obj_num = 3 + 2 * len(pages_text)
    page_obj_nums = [3 + i * 2 for i in range(len(pages_text))]
    content_obj_nums = [4 + i * 2 for i in range(len(pages_text))]

    kids = " ".join(f"{n} 0 R" for n in page_obj_nums)
    objects.append((1, "<< /Type /Catalog /Pages 2 0 R >>"))
    objects.append((2, f"<< /Type /Pages /Kids [{kids}] /Count {len(pages_text)} >>"))

    for i, text in enumerate(pages_text):
        page_num = page_obj_nums[i]
        content_num = content_obj_nums[i]
        objects.append(
            (
                page_num,
                f"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 {font_obj_num} 0 R >> >> "
                f"/MediaBox [0 0 612 792] /Contents {content_num} 0 R >>",
            )
        )
        stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET"
        objects.append((content_num, f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"))

    objects.append((font_obj_num, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))
    objects.sort(key=lambda o: o[0])

    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num, body in objects:
        offsets[num] = buf.tell()
        buf.write(f"{num} 0 obj\n{body}\nendobj\n".encode("latin-1"))

    xref_offset = buf.tell()
    max_num = max(offsets.keys())
    buf.write(f"xref\n0 {max_num + 1}\n".encode())
    buf.write(b"0000000000 65535 f \n")
    for n in range(1, max_num + 1):
        off = offsets.get(n, 0)
        buf.write(f"{off:010} 00000 n \n".encode())
    buf.write(f"trailer\n<< /Size {max_num + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode())
    return buf.getvalue()
