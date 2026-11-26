from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter

TESCOM_CA_CODES = (
    ("ATTENDANCE", "ATTENDANCE(10%)"),
    ("NOTEBOOK", "NOTEBOOK(10%)"),
    ("OPEN_DAY", "OPEN DAY(10%)"),
    ("TEXTBOOK", "TEXTBOOK(10%)"),
    ("SB_TEST", "SB TEST(10%)"),
    ("TB_ASSESS", "TB ASSESS(10%)"),
)
TESCOM_TERM_HEADERS = (
    *[label for _, label in TESCOM_CA_CODES],
    "TOTAL CA(60%)",
    "AVERAGE CA(30%)",
    "EXAM(70%)",
    "TOTAL SCORE(100%)",
)
TERM_TITLES = ("FIRST TERM", "SECOND TERM", "THIRD TERM")
TERM_START_COLS = (3, 13, 23)  # C, M, W

THIN_SIDE = Side(style="thin", color="000000")
ALL_THIN_BORDER = Border(top=THIN_SIDE, bottom=THIN_SIDE, left=THIN_SIDE, right=THIN_SIDE)
TERM_BANNER_BORDER = Border(top=THIN_SIDE, left=THIN_SIDE, right=THIN_SIDE)

FONT_SCHOOL = Font(name="Times New Roman", size=16, bold=True)
FONT_SESSION = Font(name="Times New Roman", size=12, bold=True)
FONT_META = Font(name="Times New Roman", size=11, bold=True)
FONT_TERM = Font(name="Times New Roman", size=9, bold=True)
FONT_HEADER = Font(name="Times New Roman", size=7, bold=False)
FONT_DATA = Font(name="Calibri", size=11, bold=False)
FONT_NAME = Font(name="Cambria", size=11, bold=False)
FONT_LEGEND = Font(name="Times New Roman", size=9, bold=True)

ALIGN_CENTER = Alignment(horizontal="center", vertical="center")
ALIGN_LEFT = Alignment(horizontal="left", vertical="center")
ALIGN_HEADER = Alignment(horizontal="center", vertical="center", wrap_text=True)

COL_WIDTHS = {
    "A": 4.2,
    "B": 39.5,
    "C": 4.2,
    "D": 4.2,
    "E": 4.2,
    "F": 4.2,
    "G": 4.2,
    "H": 4.2,
    "I": 5.0,
    "J": 5.0,
    "K": 4.5,
    "L": 5.5,
    "M": 4.2,
    "N": 4.2,
    "O": 4.2,
    "P": 4.2,
    "Q": 4.2,
    "R": 4.2,
    "S": 5.0,
    "T": 5.0,
    "U": 4.5,
    "V": 5.5,
    "W": 4.2,
    "X": 4.2,
    "Y": 4.2,
    "Z": 4.2,
    "AA": 4.2,
    "AB": 4.2,
    "AC": 5.0,
    "AD": 5.0,
    "AE": 4.5,
    "AF": 5.5,
    "AG": 6.5,
    "AH": 5.8,
    "AI": 5.5,
    "AJ": 6.8,
    "AK": 12.0,
}

LEGEND_SPANS = (
    ("B", None),
    ("E", "J"),
    ("L", "P"),
    ("S", "X"),
    ("Z", "AD"),
    ("AF", "AJ"),
)


@dataclass(frozen=True)
class MarkBookStudentRow:
    full_name: str
    # Mapping of (term_order, component_code) -> raw_score
    scores: Mapping[tuple[int, str], Decimal | None]


@dataclass(frozen=True)
class MarkBookSheetData:
    sheet_title: str
    school_name: str
    session_name: str
    subject_name: str
    class_name: str
    teacher_name: str
    grading_grades: Sequence[Mapping[str, Any]]
    students: Sequence[MarkBookStudentRow]


def _sanitize_sheet_title(raw: str, existing: set[str]) -> str:
    cleaned = re.sub(r"[\\/*?:\[\]]", " ", raw).strip()[:31] or "MarkBook"
    candidate = cleaned
    suffix = 2
    while candidate in existing:
        tag = f" ({suffix})"
        candidate = f"{cleaned[: 31 - len(tag)]}{tag}"
        suffix += 1
    existing.add(candidate)
    return candidate


def _excel_number(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _build_grade_formula(avg_cell: str, grades: Sequence[Mapping[str, Any]]) -> str:
    ordered = sorted(grades, key=lambda g: float(g.get("min_score", 0)), reverse=True)
    if not ordered:
        return f'=IF({avg_cell}>=70,"A", IF({avg_cell}>=60,"B", IF({avg_cell}>=50,"C", IF({avg_cell}>=45,"D", IF({avg_cell}>=40,"E","F")))))'
    if len(ordered) == 1:
        return f'="{ordered[0].get("grade", "")}"'

    expr = f'"{ordered[-1].get("grade", "")}"'
    for entry in reversed(ordered[:-1]):
        min_score = entry.get("min_score", 0)
        min_str = str(int(min_score)) if float(min_score).is_integer() else str(min_score)
        grade_label = str(entry.get("grade", "")).replace('"', "")
        expr = f'IF({avg_cell}>={min_str},"{grade_label}", {expr})'
    return f"={expr}"


def _format_legend_entry(entry: Mapping[str, Any], is_first: bool) -> str:
    grade = str(entry.get("grade", ""))
    min_val = int(float(entry.get("min_score", 0)))
    max_val = int(float(entry.get("max_score", 0)))
    remark = str(entry.get("remark", "")).upper()
    prefix = "GRADES:    " if is_first else ""
    return f"{prefix}{grade} = {min_val} - {max_val}, {remark}"


def _apply_border_range(
    ws: openpyxl.worksheet.worksheet.Worksheet,
    min_col: int,
    max_col: int,
    row: int,
    border: Border,
) -> None:
    for col in range(min_col, max_col + 1):
        ws.cell(row=row, column=col).border = border


def build_mark_book_xlsx(sheets: Sequence[MarkBookSheetData]) -> bytes:
    """Builds a TESCOM-compatible Mark Book `.xlsx` workbook matching the
    school's `MARK BOOK FOR 2026.27.xlsx` layout, fonts, borders, and formulas.
    """
    wb = openpyxl.Workbook()
    # Remove default sheet after populating sheets
    default_ws = wb.active
    used_titles: set[str] = set()

    for sheet_data in sheets:
        title = _sanitize_sheet_title(sheet_data.sheet_title, used_titles)
        ws = wb.create_sheet(title=title)
        ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.freeze_panes = "C6"

        for col_letter, width in COL_WIDTHS.items():
            ws.column_dimensions[col_letter].width = width

        ws.row_dimensions[1].height = 14.25
        ws.row_dimensions[2].height = 14.25
        ws.row_dimensions[3].height = 14.25
        ws.row_dimensions[4].height = 14.25
        ws.row_dimensions[5].height = 48

        # Row 1: School header
        ws.merge_cells("C1:AJ1")
        ws["C1"] = sheet_data.school_name.upper()
        ws["C1"].font = FONT_SCHOOL
        ws["C1"].alignment = ALIGN_CENTER

        # Row 2: Session title
        ws.merge_cells("C2:AJ2")
        ws["C2"] = f"MARK BOOK FOR {sheet_data.session_name.upper()} ACADEMIC SESSION"
        ws["C2"].font = FONT_SESSION
        ws["C2"].alignment = ALIGN_CENTER

        # Row 3: Subject, Class, Teacher
        ws.merge_cells("A3:B3")
        ws["A3"] = f"SUBJECT: {sheet_data.subject_name.upper()}"
        ws["A3"].font = FONT_META
        ws["A3"].alignment = ALIGN_LEFT

        ws["F3"] = f"CLASS: {sheet_data.class_name.upper()}"
        ws["F3"].font = FONT_META
        ws["F3"].alignment = ALIGN_LEFT

        ws["S3"] = f"TEACHER'S NAME: {sheet_data.teacher_name.upper()}"
        ws["S3"].font = FONT_META
        ws["S3"].alignment = ALIGN_LEFT

        # Row 4: Term banners
        term_ranges = ((3, 12), (13, 22), (23, 32))
        for (start_col, end_col), label in zip(term_ranges, TERM_TITLES, strict=True):
            ws.merge_cells(start_row=4, start_column=start_col, end_row=4, end_column=end_col)
            cell = ws.cell(row=4, column=start_col, value=label)
            cell.font = FONT_TERM
            cell.alignment = ALIGN_CENTER
            _apply_border_range(ws, start_col, end_col, 4, TERM_BANNER_BORDER)

        ws.merge_cells("AG4:AK4")
        ws["AG4"] = "FINAL SESSION GRADING"
        ws["AG4"].font = FONT_TERM
        ws["AG4"].alignment = ALIGN_CENTER
        _apply_border_range(ws, 33, 37, 4, TERM_BANNER_BORDER)

        # Row 5: Column headers
        ws["A5"] = "S/N"
        ws["B5"] = "STUDENT NAME"
        for start_col in TERM_START_COLS:
            for offset, header in enumerate(TESCOM_TERM_HEADERS):
                ws.cell(row=5, column=start_col + offset, value=header)

        final_headers = (
            "ANNUAL SCORE (300%)",
            "TOTAL AVERAGE (100%)",
            "GRADE",
            "POSITION",
            "TEACHER'S REMARK",
        )
        for offset, header in enumerate(final_headers):
            ws.cell(row=5, column=33 + offset, value=header)

        for col in range(1, 38):
            cell = ws.cell(row=5, column=col)
            cell.font = FONT_HEADER
            cell.alignment = ALIGN_HEADER
            cell.border = ALL_THIN_BORDER

        # Student rows
        start_row = 6
        last_row = start_row + max(len(sheet_data.students) - 1, 0)

        for idx, student in enumerate(sheet_data.students, start=1):
            r = start_row + idx - 1
            ws.row_dimensions[r].height = 15

            c_sn = ws.cell(row=r, column=1, value=idx)
            c_sn.font = FONT_DATA
            c_sn.alignment = ALIGN_CENTER

            c_name = ws.cell(row=r, column=2, value=student.full_name)
            c_name.font = FONT_NAME
            c_name.alignment = ALIGN_LEFT

            for term_order, start_col in enumerate(TERM_START_COLS, start=1):
                for offset, (code, _) in enumerate(TESCOM_CA_CODES):
                    val = _excel_number(student.scores.get((term_order, code)))
                    cell = ws.cell(row=r, column=start_col + offset, value=val)
                    cell.font = FONT_DATA
                    cell.alignment = ALIGN_CENTER

                c_first = get_column_letter(start_col)
                c_last_ca = get_column_letter(start_col + 5)
                c_tot_ca = get_column_letter(start_col + 6)
                c_avg_ca = get_column_letter(start_col + 7)
                c_exam = get_column_letter(start_col + 8)

                tot_ca_cell = ws.cell(
                    row=r, column=start_col + 6, value=f"=SUM({c_first}{r}:{c_last_ca}{r})"
                )
                tot_ca_cell.font = FONT_DATA
                tot_ca_cell.alignment = ALIGN_CENTER

                avg_ca_cell = ws.cell(row=r, column=start_col + 7, value=f"={c_tot_ca}{r}/2")
                avg_ca_cell.font = FONT_DATA
                avg_ca_cell.alignment = ALIGN_CENTER

                exam_val = _excel_number(student.scores.get((term_order, "EXAM")))
                exam_cell = ws.cell(row=r, column=start_col + 8, value=exam_val)
                exam_cell.font = FONT_DATA
                exam_cell.alignment = ALIGN_CENTER

                tot_term_cell = ws.cell(
                    row=r, column=start_col + 9, value=f"=SUM({c_avg_ca}{r},{c_exam}{r})"
                )
                tot_term_cell.font = FONT_DATA
                tot_term_cell.alignment = ALIGN_CENTER
                tot_term_cell.number_format = "0"

            # Final session grading columns (AG..AK)
            ag_cell = ws.cell(row=r, column=33, value=f"=SUM(L{r},V{r},AF{r})")
            ag_cell.font = FONT_DATA
            ag_cell.alignment = ALIGN_CENTER
            ag_cell.number_format = "0"

            ah_cell = ws.cell(row=r, column=34, value=f"=AVERAGE(L{r},V{r},AF{r})")
            ah_cell.font = FONT_DATA
            ah_cell.alignment = ALIGN_CENTER
            ah_cell.number_format = "0"

            ai_cell = ws.cell(
                row=r,
                column=35,
                value=_build_grade_formula(f"AH{r}", sheet_data.grading_grades),
            )
            ai_cell.font = FONT_DATA
            ai_cell.alignment = ALIGN_CENTER

            aj_cell = ws.cell(
                row=r,
                column=36,
                value=f"=RANK(AH{r},$AH${start_row}:$AH${last_row},0)",
            )
            aj_cell.font = FONT_DATA
            aj_cell.alignment = ALIGN_CENTER

            ak_cell = ws.cell(row=r, column=37, value=None)
            ak_cell.font = FONT_DATA
            ak_cell.alignment = ALIGN_LEFT

            _apply_border_range(ws, 1, 37, r, ALL_THIN_BORDER)

        # Footer legend row(s)
        base_legend_row = (last_row + 1) if sheet_data.students else start_row
        ordered_legend = sorted(
            sheet_data.grading_grades, key=lambda g: float(g.get("min_score", 0)), reverse=True
        )
        per_row = len(LEGEND_SPANS)
        for idx, entry in enumerate(ordered_legend):
            row_offset = idx // per_row
            span_idx = idx % per_row
            col_start, col_end = LEGEND_SPANS[span_idx]
            target_row = base_legend_row + row_offset
            if col_end:
                ws.merge_cells(f"{col_start}{target_row}:{col_end}{target_row}")
            cell = ws[f"{col_start}{target_row}"]
            cell.value = _format_legend_entry(entry, is_first=(idx == 0))
            cell.font = FONT_LEGEND
            cell.alignment = ALIGN_LEFT

    if default_ws is not None and len(wb.worksheets) > 1:
        wb.remove(default_ws)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
