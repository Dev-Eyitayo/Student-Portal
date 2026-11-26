from __future__ import annotations

from io import BytesIO

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.modules.computation.schemas import BroadsheetResponse

THIN_SIDE = Side(style="thin", color="CBD5E1")
CELL_BORDER = Border(top=THIN_SIDE, bottom=THIN_SIDE, left=THIN_SIDE, right=THIN_SIDE)
HEADER_FILL = PatternFill(fill_type="solid", start_color="0F4C5C", end_color="0F4C5C")
SUBHEADER_FILL = PatternFill(fill_type="solid", start_color="F8FAFC", end_color="F8FAFC")

FONT_SCHOOL = Font(name="Calibri", size=14, bold=True, color="0F172A")
FONT_TITLE = Font(name="Calibri", size=11, bold=True, color="334155")
FONT_HEADER = Font(name="Calibri", size=9, bold=True, color="FFFFFF")
FONT_DATA = Font(name="Calibri", size=10, bold=False, color="0F172A")
FONT_DATA_BOLD = Font(name="Calibri", size=10, bold=True, color="0F172A")

ALIGN_CENTER = Alignment(horizontal="center", vertical="center")
ALIGN_LEFT = Alignment(horizontal="left", vertical="center")
ALIGN_HEADER = Alignment(horizontal="center", vertical="center", wrap_text=True)


def build_broadsheet_xlsx(school_name: str, data: BroadsheetResponse) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = (data.class_group_name or "Broadsheet")[:31]
    ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
    ws.page_setup.paperSize = ws.PAPERSIZE_A4

    # Collect unique subjects sorted by subject_code
    subject_cols_map: dict[str, tuple[str, str]] = {}
    for student in data.students:
        for subj in student.subjects:
            subject_cols_map.setdefault(subj.subject_code, (subj.subject_code, subj.subject_name))
    subject_cols = sorted(subject_cols_map.values(), key=lambda x: x[0])

    include_class_col = data.class_group_id is None
    include_annual = any(s.annual_score is not None for s in data.students)

    headers: list[str] = ["S/N", "Admission No", "Student Name"]
    if include_class_col:
        headers.append("Class")
    for code, name in subject_cols:
        headers.append(f"{code}\n{name}")
    headers.extend(
        [
            "Total Obtained",
            "Total Obtainable",
            "Average (%)",
            "Class Pos",
            "Level Pos",
        ]
    )
    if include_annual:
        headers.extend(["Annual (%)", "Promotion"])

    total_cols = max(len(headers), 6)
    last_col_letter = get_column_letter(total_cols)

    ws.merge_cells(f"A1:{last_col_letter}1")
    ws["A1"] = school_name.upper()
    ws["A1"].font = FONT_SCHOOL
    ws["A1"].alignment = ALIGN_CENTER
    ws.row_dimensions[1].height = 22

    ws.merge_cells(f"A2:{last_col_letter}2")
    ws["A2"] = f"CLASS BROADSHEET — {data.class_group_name} ({data.academic_term_name})"
    ws["A2"].font = FONT_TITLE
    ws["A2"].alignment = ALIGN_CENTER
    ws.row_dimensions[2].height = 18

    header_row = 4
    ws.row_dimensions[header_row].height = 34
    for col_idx, text in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col_idx, value=text)
        cell.font = FONT_HEADER
        cell.fill = HEADER_FILL
        cell.alignment = ALIGN_HEADER
        cell.border = CELL_BORDER

    freeze_col = "E" if include_class_col else "D"
    ws.freeze_panes = f"{freeze_col}5"

    # Column widths
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 28
    col_offset = 4
    if include_class_col:
        ws.column_dimensions["D"].width = 16
        col_offset = 5
    for idx in range(len(subject_cols)):
        ws.column_dimensions[get_column_letter(col_offset + idx)].width = 12
    summary_start = col_offset + len(subject_cols)
    for idx in range(summary_start, len(headers) + 1):
        ws.column_dimensions[get_column_letter(idx)].width = 13

    # Student rows
    for idx, student in enumerate(data.students, start=1):
        r = header_row + idx
        ws.row_dimensions[r].height = 18
        subj_by_code = {s.subject_code: s.total_score for s in student.subjects}

        row_values: list[object] = [
            idx,
            student.admission_number,
            f"{student.last_name} {student.first_name}".strip(),
        ]
        if include_class_col:
            row_values.append(student.class_group_name or "-")
        for code, _ in subject_cols:
            score = subj_by_code.get(code)
            row_values.append(float(score) if score is not None else "-")
        row_values.extend(
            [
                float(student.total_obtained),
                float(student.total_obtainable),
                float(student.percentage),
                student.class_position or "-",
                student.level_position or "-",
            ]
        )
        if include_annual:
            row_values.append(
                float(student.annual_score) if student.annual_score is not None else "-"
            )
            row_values.append(
                student.promotion_decision.value.replace("_", " ")
                if student.promotion_decision
                else "-"
            )

        for col_idx, val in enumerate(row_values, start=1):
            cell = ws.cell(row=r, column=col_idx, value=val)
            cell.font = FONT_DATA_BOLD if col_idx == 3 else FONT_DATA
            cell.alignment = ALIGN_LEFT if col_idx == 3 else ALIGN_CENTER
            cell.border = CELL_BORDER
            if idx % 2 == 0:
                cell.fill = SUBHEADER_FILL

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
