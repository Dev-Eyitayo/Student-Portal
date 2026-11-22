# Olivet Frontend Pages, Design System & Figma Make Spec

This document defines the visual design system, page-by-page information architecture, role-based access rules, and batched **Figma Make** prompts for the Olivet Academic Records Management Platform.

- **Figma Design Reference:** [https://flint-mower-06389066.figma.site](https://flint-mower-06389066.figma.site)
- **Roles & Permissions Matrix:** [personas-and-permissions.md](./personas-and-permissions.md)
- **Ticket Assignments & Exact Endpoints:** [Olivet_Frontend_Tickets.xlsx](./Olivet_Frontend_Tickets.xlsx)

It uses natural Nigerian school terminology (**Class Teacher**, **Class Arm**, **Subject Teacher**, **CA & Exam**, **Broadsheet**, **Admission Number**, **Parent / Guardian**, **Report Card**) and maps every screen to live backend endpoints in [Olivet_Frontend_Tickets.xlsx](./Olivet_Frontend_Tickets.xlsx).

---

## 1. Design System Brief (`FE-00A` / `FE-00B`)

### 1.1 Implementation Base
- **Recommended UI Kit:** `shadcn/ui` (React + Tailwind CSS + Radix UI primitives) for code, paired with a `shadcn/ui` or `Untitled UI` Figma Community kit for design handoff.
- **Iconography:** `Lucide Icons` (1.5px stroke, 16px in buttons/inputs, 18px in sidebar).

### 1.2 Color Palette (Deep Petrol & Warm Paper)
All colors must be wired as CSS custom properties (`--primary`, `--primary-hover`, `--background`, `--surface`, `--border`, `--foreground`) in `globals.css` so switching themes—or supporting per-school custom accent colors later—requires changing only the token values, with zero component rewrites.

| Token Name | CSS Variable | Hex | Role |
| --- | --- | --- | --- |
| `Deep Petrol` | `--primary` | `#0F4C5C` | Primary brand color, primary CTAs, active navigation state, report card header accent |
| `Petrol Hover` | `--primary-hover` | `#0B3B48` | Hover/active state for primary buttons and links |
| `Warm Paper` | `--background` | `#FAF9F6` | Application background behind content |
| `Ledger White` | `--surface` | `#FFFFFF` | Cards, tables, drawers, modals, and printable report card sheet |
| `Slate Ink` | `--foreground` | `#0F172A` | Primary headings and table text (`#475569` secondary text, `#E2E8F0` hairline borders) |
| `Academic Ochre` | `--accent` | `#B45309` | Subtle accent for current term indicators, pending review badges (`#FEF3C7` soft fill) |

Semantic status pills (soft fill + dark text, no neon gradients):
- **Approved / Present / Promoted / Published:** `#DCFCE7` background, `#15803D` text
- **Submitted / Pending / Draft:** `#FEF3C7` background, `#B45309` text
- **Absent / Withdrawn / Repeat / Locked:** `#FFE4E6` background, `#BE123C` text

### 1.3 Typography & Numbers
- **Headings & Navigation:** `Instrument Sans` (SemiBold 600 for page titles, Medium 500 for tabs/labels).
- **Dense Tables, Forms & Body:** `IBM Plex Sans` (Regular 400 / Medium 500 — chosen for high-legibility institutional ledgers instead of generic Inter/Jakarta defaults).
- **Printable Report Card Header (Optional Accent):** `Source Serif 4` for the school name on `/results/verify/:token`.
- **Numeric Alignment:** Apply `font-variant-numeric: tabular-nums` on all scores, totals, averages, ranks, attendance counts, and admission numbers so columns align like a ledger.

### 1.4 Anti-AI-Slop & Copywriting Rules
- **Crisp borders over heavy shadows:** Use `1px solid #E2E8F0` on cards and tables. Reserve shadows only for dropdowns, modals, and slide-over drawers.
- **No decorative clutter:** No purple/blue gradients, no glassmorphism, no ALL-CAPS tracked eyebrow labels above headings, no middle-dot meta strings (`A · B · C`), and no `→` arrows appended to button text.
- **Active-voice, plain copy:** Use clear action labels (`Save scores`, `Assign Class Teacher`, `Publish Results`, `Send Results via Email`) instead of generic `Submit` or backend jargon.
- **Actionable empty states:** Tell staff what is missing and give the direct action button (e.g., *"No students enrolled in SS1 O yet. Enroll a student or upload an Excel sheet."*).

### 1.5 Core Reusable Patterns
1. **App Shell:** Collapsible left sidebar (grouped into *Workspace*, *Academics*, *Results*, *Settings*) + top bar showing **School Crest & Name**, **Active Session & Term pill**, and **User Profile / Role menu**.
2. **Page Header + Underline Tabs:** Page title + 1-line description on the left, primary CTA on the right, underline tabs directly beneath when a page has multiple views.
3. **Metric / KPI Card Row:** 3 or 4 compact cards at the top of operational pages. Each card shows: `Label`, `Primary Number (tabular-nums)`, and `1 short helper note` (or clickable filter trigger).
4. **Filter Toolbar:** Left-aligned search box + **Dropdown Filters** (Session, Term, Level, Class Arm, Status) + `Reset filters` link when active.
5. **Data Table + Kebab (`⋮`) Row Menu + Slide-over Drawer:** Sticky header, compact 44px–48px row height, status pill column, **kebab (`⋮`) action menu** in the rightmost column for row actions (avoids cramming multiple buttons per row; keep inline buttons only for high-frequency tasks like Attendance toggles or Teacher `Enter Scores`), pagination footer, and right-side slide-over drawer for quick edits without losing table context.

---

## 2. Page-by-Page Specification (13 Pages)

### Public Pages (No Sidebar)

#### Page 1: Sign In & Accept Invite (`/login`, `/accept-invite`)
- **Layout:** Clean two-column split screen. Left side: calm Olivet institutional panel (school crest/emblem + concise 2-line value statement). Right side: focused form.
- **Sign In (`/login`):** Email, Password (show/hide toggle), `Sign in` button, inline human-friendly error banner.
- **Accept Invite (`/accept-invite`):** Reads invite token from URL, displays invited school, Full Name, email, and assigned role badge (from `verify-invite`), asks for New Password + Confirm Password, `Activate account` CTA.
- **Endpoints:** `POST /api/v1/identity/login`, `GET /api/v1/identity/staff/verify-invite`, `POST /api/v1/identity/staff/accept-invitation`.

#### Page 2: School Registration (`/onboarding`)
- **Layout:** Centered stepper card (`max-w-xl`) on `#F8FAFC` canvas.
- **Fields:**
  - Step 1: School Name, Official Code, School Type selector (**Primary (Basic 1–6)** vs **Secondary (JSS1–SS3)**), State, City, Address.
  - Step 2: Admin First Name, Last Name, Email, Password.
- **CTA:** `Create school account`.
- **Endpoint:** `POST /api/v1/identity/schools`.

#### Page 3: Parent / Student Result Checker (`/results/verify/:token`, `/results/verify-qr`)
- **Layout:** Official document viewer on `#F8FAFC` backdrop—must look like an authentic Nigerian school statement of result, not a generic SaaS dashboard.
- **State 1 — Verification Gate:** School Name & Crest, short prompt *"Enter the student's date of birth to view this terminal report card"*, Date of Birth input, `View Report Card` button.
- **State 2 — Printable Report Card Sheet:**
  - **Header:** School Logo/Crest, School Name & Address, `"FIRST TERM REPORT CARD • 2026/2027 SESSION"`.
  - **Student Strip:** Full Name, Admission Number, Class Arm (`SS1 O` or `Basic 1 A`), Department (hidden when `null` for Primary/JSS), Term Average %, Position / Rank (if enabled).
  - **Attendance Summary:** `Times School Opened`, `Times Present`, `Times Absent`.
  - **Subject Score Table:** `Subject`, `CA Total`, `Exam`, `Total`, `Grade`, `Class Position`, `Remark` (`tabular-nums`).
  - **Remarks & Signatures:** `Class Teacher's Remark`, `Principal's / Head Teacher's Remark`, Principal Signature image, QR Authenticity Verification badge, and `Print / Save PDF` button.
- **Endpoints:** `GET /api/v1/results/verify/{token}`, `GET /api/v1/results/verify-qr`.

---

### Authenticated Pages (Inside App Shell)

#### Page 4: Home / Dashboard (`/dashboard` & `/dashboard/issues`)
- **Admin / Principal / Academic Officer View:**
  - **Metric Cards (4):** `Students Enrolled`, `Active Staff`, `Score Sheets Approved`, `Current Term Status` (`Draft` / `Published`).
  - **Setup Checklist Banner:** Visible if `/schools/{id}/setup` has incomplete items, with `Continue setup` → `/setup`.
  - **Term Progress Tracker:** 5-step horizontal pipeline (`1. Classes & Subjects` → `2. Scores Entered` → `3. Scores Approved` → `4. Attendance Completed` → `5. Results Published`).
  - **Needs Attention Feed:** Actionable blockers with direct drill-downs:
    - Missing Class Teacher (`SS1 L — No Class Teacher assigned`) → opens `/structure/classes/:class_group_id` (or Assign Class Teacher drawer).
    - Pending Score Approval (`JSS2 O Mathematics — Review scores`) → opens `/assessments?tab=approvals&term_id=...&class_group_id=...&class_subject_id=...`.
    - `View all term issues` → opens **`/dashboard/issues`** (full filterable Term Readiness & Issues page backed by `GET /api/v1/structure/dashboard/issues`).
  - **Recent Activities Feed:** Latest school actions (score submissions, approvals, invitations, publications) from `GET /api/v1/audit/logs` (or `recent_activities` on `dashboard/admin`).
- **Teacher View:**
  - **Metric Cards (3):** `My Class` (e.g., `SS1 O` or `Not Assigned`), `Subjects I Teach`, `Pending Score Sheets`.
  - **My Class (Single Highlighted Card):** Displays the teacher's assigned class arm, student count, today's attendance status (`Marked` / `Not Marked`), and 3 direct drill-down buttons:
    - `Mark Attendance` → `/attendance?tab=roll-call&class_group_id=...&date=today`
    - `View Class Subjects` → `/structure/classes/:class_group_id`
    - `Write Class Teacher Remarks` → `/results?tab=remarks&term_id=...&class_group_id=...`
  - **Subjects I Teach (Table):** Subject, Class Arm, Department, Score Sheet Status (`Draft` / `Submitted` / `Approved`), and `Enter Scores` → `/assessments?tab=enter-scores&term_id=...&class_subject_id=...`.
- **Endpoints:** `GET /api/v1/structure/dashboard/admin`, `GET /api/v1/structure/dashboard/issues`, `GET /api/v1/structure/dashboard/teacher`, `GET /api/v1/audit/logs`, `GET /api/v1/identity/me`, `GET /api/v1/class-subjects/my-assignments`, `GET /api/v1/identity/schools/{id}/setup`.

#### Page 5: Guided Setup Wizard (`/setup`)
- **Layout:** Horizontal 4-step wizard with `Save & Continue` and `Skip for now`.
- **Steps:**
  1. `School Profile & Branding` (Upload School Logo & Principal Signature, School Timezone).
  2. `CA & Grading Rules` (Default Olivet `10/10/10/70` CA/Exam preview or custom weights, Pass Mark, Ranking & Promotion rules).
  3. `Create Classes from Template` (Preview and apply Primary `Basic 1–6` or Secondary `JSS1–SS3` with arms).
  4. `Review & Launch` (Summary checklist + `Go to Dashboard`).
- **Endpoints:** `GET/PATCH /api/v1/identity/schools/{id}/setup`, `POST /api/v1/identity/schools/{id}/branding/uploads?asset_type=...`, `GET /api/v1/identity/onboarding/templates`, `POST /api/v1/structure/templates/apply`.

#### Page 6: Staff & Roles (`/staff`)
- **Tabs:** `All Staff` | `Roles & Permissions`
- **Metric Cards (4):** `Total Staff`, `Active Teachers`, `Pending Invites`, `Deactivated`.
- **Dropdown Filters:** `Role`, `Status` (`Active`, `Pending Invite`, `Inactive`), `Department`.
- **Table Columns:** `Name & Email`, `Role`, `Department`, `Status`, `Actions` (`Edit Access`, `Deactivate`).
- **Drawers:**
  - `Invite Staff`: Email, Role, Staff Type.
  - `Manage Permissions`: Baseline Role + Scoped Permission Grants (`Whole School`, `Department`, `Academic Level`, `Specific Class Arm`).
- **Endpoints:** `GET /api/v1/identity/users`, `GET /api/v1/identity/permissions`, `GET /api/v1/identity/users/{id}/permissions`, `POST /api/v1/identity/staff/invite`, `POST /api/v1/identity/users/{id}/permissions`, `DELETE /api/v1/identity/users/{id}/permissions/{grant_id}`, `POST /api/v1/identity/users/{id}/deactivate`.

#### Page 7: Classes & Terms (`/structure` & `/structure/classes/:id`)
- **Tabs:** `Sessions & Terms` | `Levels & Departments` | `Class Arms & Class Teachers`
- **Metric Cards (4):** `Active Session`, `Current Term`, `Total Class Arms`, `Classes Without a Class Teacher`.
- **Dropdown Filters:** `Session`, `Level` (`JSS1–SS3` / `Basic 1–6`), `Department` *(hidden for Primary schools)*, `Class Teacher` (`All`, `Assigned`, `Unassigned`).
- **Class Arms Table:** `Class Arm` (`SS1 O`), `Level`, `Department`, `Class Teacher`, `Students`, `Actions` (`View Class`, `Assign Class Teacher`).
- **Class Detail Page (`/structure/classes/:id`):**
  - Header card showing **Class Teacher** (with `Change Class Teacher` modal/drawer).
  - **Subjects & Subject Teachers Table** (from `GET /api/v1/structure/class-groups/{id}/subjects`): lists every subject taken by the class and its assigned teacher(s), with quick `Assign Teacher` button.
- **Endpoints:** `/api/v1/structure/sessions`, `/api/v1/structure/sessions/{session_id}/terms`, `/api/v1/structure/terms/{term_id}`, `/levels`, `/departments`, `/arms`, `/class-groups`, `GET /api/v1/structure/class-groups/{id}/subjects`.

#### Page 8: Students (`/students` & `/students/:id`)
- **Tabs:** `Student Directory` | `Bulk Upload (Excel)`
- **Metric Cards (4):** `Total Students`, `Enrolled This Session`, `Not Yet Enrolled`, `Withdrawn / Graduated`.
- **Dropdown Filters:** `Status` (`Active`, `Graduated`, `Withdrawn`, `Transferred`), `Gender`, `Level`, `Class Arm`.
- **Student Directory Table:** `Admission No.`, `Full Name`, `Gender`, `Current Class`, `Parent / Guardian`, `Status`, `Actions`.
- **Bulk Upload Tab:** Download Excel template, drag-and-drop `.xlsx`, job status tracker.
- **Student Profile (`/students/:id`):** Bio & Parent/Guardian contact card, Class Enrollment history, **Registered Subjects** tab (for Senior Secondary students to add/drop Elective & Trade subjects).
- **Endpoints:** `GET /api/v1/students`, `GET /api/v1/students/summary`, `/api/v1/students/bulk-import`, `/api/v1/students/{id}/enrollments`, `/api/v1/enrollments/{id}/subject-registrations`.

#### Page 9: Subjects (`/subjects`)
- **Tabs:** `Subject Catalog` | `Level Curriculum` | `Class Subject Teachers` | `My Teaching Subjects`
- **Metric Cards (4):** `Total Subjects`, `Class Subject Allocations`, `Subjects Without a Teacher`, `Elective / Trade Subjects`.
- **Dropdown Filters:** `Session`, `Level`, `Department`, `Category` (`Core`, `Elective`, `Trade`), `Teacher Assigned` (`Yes`, `No`).
- **Actions:** `Add Subject`, `Assign Subject Teacher` (single or bulk across arms).
- **Endpoints:** `/api/v1/subjects`, `GET /api/v1/subjects/summary`, `/api/v1/subjects/offerings`, `/api/v1/class-subjects`, `/api/v1/class-subjects/{id}/teacher-assignments`, `/api/v1/class-subjects/my-assignments`.

#### Page 10: Attendance (`/attendance`)
- **Tabs:** `Daily Roll Call` | `Term Attendance Summary`
- **Metric Cards (4):** `Days School Opened`, `Present Today (%)`, `Classes Marked Today`, `Manual Summary Overrides`.
- **Dropdown Filters:** `Term`, `Class Arm` *(pre-selected for a Class Teacher)*, `Date Picker` *(Daily Roll Call)*, `Source` (`Daily Roll Call`, `Manual Total` — *Term Attendance Summary tab*).
- **Daily Roll Call Tab:** Student list with fast `Present` / `Absent` segmented toggle per student + `Backdate Reason` field (appears automatically when marking a past date) + `Save Roll Call`.
- **Term Attendance Summary Tab:** Shows `Times School Opened`, `Times Present`, `Times Absent` per student with `Edit Manual Total` drawer.
- **Endpoints:** `GET /api/v1/structure/attendance/summary`, `GET/POST /api/v1/structure/class-groups/{id}/attendance`, `GET/PATCH /api/v1/structure/class-groups/{id}/attendance-summary`, `PATCH /api/v1/enrollments/{id}/attendance-summary`.

#### Page 11: Scores & CA (`/assessments`)
- **Tabs:** `CA & Exam Setup` | `Enter Scores` | `Submissions & Approvals`
- **Metric Cards (4):** `Total Score Sheets`, `Draft / In Progress`, `Submitted (Awaiting Approval)`, `Approved`.
- **Dropdown Filters:** `Term`, `Class Arm`, `Subject`, `Approval Status` (`Draft`, `Submitted`, `Approved`).
- **Enter Scores Grid:** Spreadsheet-like table showing each student (`Admission No.`, `Name`), columns for each assessment component (`1st Test /10`, `2nd Test /10`, `Assignment /10`, `Exam /70`), `Status` dropdown (`Scored`, `Absent`, `Exempt`), auto-calculated `Total`, and sticky footer with `Save Scores` + `Submit for Approval`.
- **Submissions & Approvals Tab:** Review queue for Academic Officers / Principals with `Approve Scores` and `Unlock for Editing` (requires reason).
- **Endpoints:** `GET /api/v1/assessments/sheets`, `/api/v1/assessments/definitions`, `GET /api/v1/assessments/scores`, `/api/v1/assessments/scores/bulk-entry`, `/api/v1/assessments/submit`, `/approve`, `/unlock`.

#### Page 12: Results & Broadsheets (`/results`)
- **Tabs:** `Class Broadsheet` | `Report Card Remarks` | `Publish & Send Results`
- **Metric Cards (4):** `Students Computed`, `Class Average (%)`, `Pass Rate (%)`, `Result Status` (`Draft` / `Published`).
- **Dropdown Filters:** `Term`, `Class Arm`, `Promotion Status` (`Promoted`, `Promoted on Trial`, `Repeating`).
- **Class Broadsheet Tab:** `Compute Term Results` button + horizontal master score matrix (students as rows, subjects as columns, total, average, position) + `View Cumulative`.
- **Report Card Remarks Tab:** Table of students in the selected class with inline/drawer inputs for **Class Teacher's Remark** and **Principal's / Head Teacher's Remark** (`Save Remarks`). Automatically shows a read-only lock banner once the term is published.
- **Publish & Send Results Tab:** `Publish Term Results` button → displays one-time parent result link table + `Download Links CSV` + `Send Results via Email` button (triggers Senviok email delivery).
- **Endpoints:** `/api/v1/computation/calculate-term`, `/broadsheet/{id}` *(also returns cumulative/annual scores)*, `GET /api/v1/publication/report-cards`, `PATCH /api/v1/publication/report-cards/{id}/remarks`, `POST /api/v1/publication/publish-term`, `POST /api/v1/publication/notify-term`.

#### Page 13: School & Account Settings (`/settings`)
- **Tabs:** `School Profile & Branding` | `Grading & Result Rules` | `My Account & Security`
- **Content:** Logo & signature uploader, grading bands (`A 70–100`, `B 60–69`, etc.), pass mark, annual promotion thresholds (`promoted_min_percentage`, `trial_min_percentage`), profile update, and password change.
- **Endpoints:** `GET/PATCH /api/v1/identity/schools/{id}/setup`, `GET/PATCH /api/v1/identity/schools/{id}/configuration`, `POST /api/v1/identity/schools/{id}/branding/uploads?asset_type=...`, `GET/PATCH /api/v1/identity/me`, `POST /api/v1/identity/change-password`.

---

## 3. Batched Figma Make Prompts (6 Prompts Total — Full Design Freedom)

To conserve free-plan prompts and let Figma Make do its best creative work, the pages, modals, and polish passes are grouped into **5 consolidated prompts**. Each prompt instructs Figma Make to **act as a Senior Product Designer** with **full layout freedom** on every page—specifying the design tokens, Nigerian school terminology, anti-AI-slop guardrails, and required capabilities/data without dictating rigid layouts.

### Prompt 1: Design System, App Shell, Dashboards & Public Auth (5 Views)
> **Role & Creative Direction:** Act as a Senior Product Designer at a top design studio designing **Olivet**, a multi-tenant academic records, attendance, grading, and report card platform for Nigerian primary and secondary schools. You have **full design and layout freedom** across every page—make intentional, high-craft layout decisions rather than falling back to generic templates.
>
> **Design System & Guardrails:**
> - **Colors (CSS-variable ready):** Background `#FAF9F6` (Warm Paper), Surfaces `#FFFFFF`, Subtle Stone `#F3F1EC`, Hairline Borders `1px solid #E2E8F0` (no heavy drop shadows, no purple/blue gradients, no glassmorphism), Primary Brand `#0F4C5C` (Deep Petrol), Hover `#0B3B48`, Primary Text `#0F172A`, Secondary Text `#475569`. Status pills: `#DCFCE7`/`#15803D` (Approved/Present/Promoted), `#FEF3C7`/`#B45309` (Submitted/Pending/Draft), `#FFE4E6`/`#BE123C` (Absent/Withdrawn/Repeat).
> - **Typography:** `Instrument Sans` for headings/navigation + `IBM Plex Sans` for tables/forms, with `tabular-nums` on all scores, ranks, attendance counts, and admission numbers. Avoid Inter and Plus Jakarta Sans.
> - **Anti-slop rules:** No ALL-CAPS eyebrow labels above headings, no middle-dot meta strings, no `→` arrows on buttons, no identical shadow-card clutter. Use plain, active-voice Nigerian school terminology (`Class Teacher`, `Class Arm`, `Subject Teacher`, `CA & Exam`, `Broadsheet`, `Admission Number`, `Parent / Guardian`) and realistic Nigerian data (`Olivet Baptist High School, Oyo`, `2026/2027 Session`, `First Term`, `SS1 O`, `Chinedu Okafor`, `Amina Yusuf`, `Mrs. Folake Adebayo`).
>
> **Build the App Shell + these 5 views with working client-side routing:**
> 1. **App Shell (for authenticated pages):** Navigation covering `Home`, `Setup Wizard`, `Staff & Roles`, `Classes & Terms`, `Students`, `Subjects`, `Attendance`, `Scores & CA`, `Results & Broadsheets`, and `School Settings`, plus school context (`Olivet Baptist High School, Oyo`), active term indicator (`2026/2027 • First Term`), an Admin/Teacher view switcher for previewing both roles, and user profile menu (`Sign out` → `/login`).
> 2. **Admin Dashboard (`/dashboard` — Admin view):** Metric cards (`Students Enrolled: 1,240`, `Active Staff: 54`, `Score Sheets Approved: 28 / 36`, `Term Status: Draft`), term workflow progress (`Classes & Subjects` → `Scores Entered` → `Scores Approved` → `Attendance` → `Results Published`), **Needs Attention** feed (`SS1 L — No Class Teacher assigned` linking to `/structure/classes/ss1-l`, `JSS2 O Mathematics — Review scores` linking to `/assessments?tab=approvals`, plus `View all term issues` linking to `/dashboard/issues`), and a **Recent Activities** feed (score submissions, approvals, invitations).
> 3. **Teacher Dashboard (`/dashboard` — Teacher view):** Metric cards (`My Class: SS1 O`, `Subjects I Teach: 4`, `Pending Score Sheets: 2`), a clear focus on the teacher's single assigned class arm (`SS1 O (Science)`, `42 Students`, today's attendance status, quick actions: `Mark Attendance` → `/attendance`, `View Class Subjects` → `/structure/classes/ss1-o`, `Write Class Teacher Remarks` → `/results?tab=remarks`), plus their **Subjects I Teach** list (`Chemistry — SS1 O`, `Chemistry — SS1 L`, `Basic Science — JSS2 O`) with score status pills and `Enter Scores` → `/assessments?tab=enter-scores`.
> 4. **Staff Sign In & Accept Invite (`/login` & `/accept-invite` — public, no sidebar):**
>    - `/login`: Email, Password (visibility toggle), Keep me signed in, `Sign in` → `/dashboard`, link to `/onboarding`.
>    - `/accept-invite`: Shows invited school (`Olivet Baptist High School, Oyo`), Full Name, email (`tunde.balogun@olivet.edu.ng`), and assigned role (`Teacher`) read-only from invite verification, plus Create Password, Confirm Password + strength indicator, and `Activate account`.
> 5. **School Registration (`/onboarding` — public, no sidebar):** 2-step school signup (`Step 1: School Profile` with clear selection between **Nigerian Primary (Basic 1–6 • Arms A–D)** and **Nigerian Secondary (JSS1–SS3 • Science/Arts/Commercial • Arms O–L–I–V–E–T)**, School Name, Official Code, State, City, Address; `Step 2: Principal / Admin Account`, `Create school account`).

---

### Prompt 2: Term Issues, Setup, Settings, Staff, Classes & Students (6 Pages)
> **Keep all existing pages, routes (`/dashboard`, `/login`, `/accept-invite`, `/onboarding`), navigation, and Deep Petrol (`#0F4C5C`) / Warm Paper (`#FAF9F6`) design system intact—do NOT overwrite or remove them. Continue acting as a Senior Product Designer with full layout freedom and add these 6 pages to the existing app (use a clean `⋮` kebab overflow menu for row actions in all data tables rather than multiple inline buttons per row, and wire all drill-down links from the Dashboard):**
>
> 1. **Term Readiness & Issues (`/dashboard/issues`):** Dedicated page opened when clicking `View all term issues` on the Admin Dashboard. Filterable by Issue Type (`Missing Class Teacher`, `Unassigned Subject`, `Scores Awaiting Approval`, `Pending Score Entry`) and Class Arm, with a direct action button on each row (`Assign Class Teacher` → `/structure/classes/:id`, `Review Scores` → `/assessments?tab=approvals`, etc.).
> 2. **Guided Setup Wizard (`/setup`):** Multi-step onboarding wizard (`School Profile & Logo`, `CA & Grading Rules`, `Create Classes from Template`, `Review & Finish`), showcasing the template selection step for **Nigerian Secondary (JSS1–SS3, Science/Arts/Commercial, Arms O-L-I-V-E-T)** and **Nigerian Primary (Basic 1–6, Arms A–D)**.
> 3. **School Settings (`/settings`):** Tabbed settings (`School Profile & Branding` | `Grading & Result Rules` | `My Account & Security`), showing grading scale rows (`A: 70–100` down to `F: 0–39`), Pass Mark (`50%`), annual promotion thresholds (`Promoted Min %`, `Trial Min %`), logo/signature uploads, and `Save changes`.
> 4. **Staff & Roles (`/staff`):** Tabbed view (`All Staff` | `Roles & Permissions`), metric cards (`Total Staff: 54`, `Teachers: 46`, `Pending Invites: 3`, `Deactivated: 2`), search + dropdown filters (`Role`, `Status`, `Department`), `Invite Staff` action, staff directory, and a way to assign scoped permissions (`Whole School`, `Department`, `Level`, or `Specific Class Arm`).
> 5. **Classes & Terms (`/structure` + `/structure/classes/:id`):**
>    - `/structure`: Tabbed view (`Sessions & Terms` | `Levels & Departments` | `Class Arms & Class Teachers`), metric cards (`Active Session: 2026/2027`, `Current Term: First Term`, `Total Class Arms: 24`, `Without Class Teacher: 2`), dropdown filters (`Session`, `Level`, `Department`, `Class Teacher`), and class arms list (`SS1 O`, `SS1 L`, `JSS1 O`) linking to `/structure/classes/:id`.
>    - `/structure/classes/:id`: Class detail view for `SS1 O` showing its **Class Teacher** (`Mrs. Folake Adebayo`, with option to change) and **Subjects Offered & Subject Teachers** (`Mathematics — Mr. Tunde Balogun`, `Chemistry — Mrs. Grace Ojo`, etc.).
> 6. **Students (`/students` + `/students/:id`):** Tabbed view (`Student Directory` | `Bulk Upload (Excel)`), metric cards (`Total Students: 1,240`, `Enrolled This Session: 1,195`, `Not Enrolled: 45`, `Withdrawn / Graduated: 18`), search + dropdown filters (`Status`, `Gender`, `Level`, `Class Arm`), `Add Student` action, and student list (`Admission No.`, `Student Name`, `Gender`, `Class Arm`, `Parent / Guardian`, `Status`) linking to `/students/:id` (student profile + registered elective/trade subjects).

---

### Prompt 3: Subjects, Attendance, Scores & Results Portal (4 Pages)
> **Keep all existing pages, routes, navigation, and Deep Petrol (`#0F4C5C`) / Warm Paper (`#FAF9F6`) design system intact—do NOT overwrite or remove them. Continue acting as a Senior Product Designer with full layout freedom and add these final 4 pages to the existing app (use a `⋮` kebab menu for row actions in management tables while keeping inline controls for Attendance toggles and Score Entry inputs, and support deep-link query params like `?tab=approvals`, `?tab=enter-scores`, `?tab=roll-call`, `?tab=remarks` from the Dashboard & Term Issues pages):**
>
> 1. **Subjects (`/subjects`):** Tabbed view (`Subject Catalog` | `Level Curriculum` | `Subject Teachers` | `My Teaching Subjects`), metric cards (`Total Subjects: 22`, `Class Allocations: 148`, `Without Teacher: 4`, `Elective / Trade: 6`), dropdown filters (`Session`, `Level`, `Department`, `Category`, `Teacher Assigned`), and class-subject teacher allocation view.
> 2. **Attendance (`/attendance`):** Tabbed view (`Daily Roll Call` | `Term Attendance Summary`), metric cards (`Days School Opened: 42`, `Present Today: 94.2%`, `Classes Marked Today: 21 / 24`, `Manual Overrides: 2`), dropdown filters (`Term: First Term`, `Class Arm: SS1 O`, `Date: Today`), and a fast daily roll-call marking experience (`Present` / `Absent` per student + optional backdate reason + `Save Roll Call`).
> 3. **Scores & CA (`/assessments`):** Tabbed view (`CA & Exam Setup` | `Enter Scores` | `Submit & Approve`), metric cards (`Total Score Sheets: 148`, `Draft: 12`, `Awaiting Approval: 8`, `Approved: 128`), dropdown filters (`Term`, `Class Arm: SS1 O`, `Subject: Chemistry`, `Status`), and a high-legibility spreadsheet-style Score Entry Grid (`Admission No.`, `Student Name`, `1st Test /10`, `2nd Test /10`, `Assign. /10`, `Exam /70`, `Total /100`, `Status` dropdown `Scored`/`Absent`/`Exempt`) with `Save Scores` and `Submit for Approval`.
> 4. **Results, Broadsheets & Public Report Card (`/results` + `/results/verify/:token`):**
>    - `/results`: Tabbed view (`Class Broadsheet` | `Report Card Remarks` | `Publish & Send Results`), metric cards (`Students Computed: 42`, `Class Average: 68.4%`, `Pass Rate: 90.5%`, `Term Status: Draft`), dropdown filters (`Term`, `Class Arm: SS1 O`, `Promotion Status`), horizontal Class Broadsheet matrix (`Student`, `ENG`, `MTH`, `CHM`, `PHY`, `BIO`, `Total`, `Avg %`, `Pos`), `Compute Results` action, and a preview link to `/results/verify/demo`.
>    - `/results/verify/:token` (public view, no sidebar): An authentic, print-ready **Official Nigerian Terminal Report Card** (`Olivet Baptist High School, Oyo`, `"FIRST TERM REPORT CARD • 2026/2027"`, Student Bio & Attendance `Opened: 60 • Present: 58 • Absent: 2`, Subject Breakdown `CA /30`, `Exam /70`, `Total /100`, `Grade`, `Position`, `Remark`, `Class Teacher's Remark`, `Principal's Remark`, Principal Signature, QR Authenticity Verification badge, and `Print / Save PDF` button).

---

### Prompt 4: Modals, Drawers, Edit Flows, Lock States & Mobile Views
> **Keep all existing pages, routes, navigation, and Deep Petrol (`#0F4C5C`) / Warm Paper (`#FAF9F6`) design system intact—do NOT overwrite or remove any existing pages. Continue acting as a Senior Product Designer and wire all interactive kebab menus, modals, slide-over drawers, confirmation dialogs, workflow states, and responsive mobile views across the existing pages:**
>
> 0. **Make Every `⋮` Kebab Row Menu Interactive (DropdownMenu with click-outside close):** Every `⋮` button in data tables must open a working dropdown menu with real actions wired to the modals/drawers below:
>    - `/staff`: `Edit Staff`, `Manage Permissions`, `Deactivate Account`.
>    - `/structure` (Sessions, Terms, Levels, Departments, Arms, Class Arms): `Edit`, `Set as Current` (sessions/terms), `Lock / Unlock Scoring` (terms), `Assign Class Teacher` (class arms), `View Class Detail`, `Retire`.
>    - `/structure/classes/:id`: `Assign / Change Subject Teacher`, `Bulk Register Electives`, `Remove Subject from Class`.
>    - `/students`: `View Profile`, `Edit Student`, `Enroll in Class Arm`, `Change Status`.
>    - `/subjects`: `Edit Subject`, `Assign Teacher`, `Bulk Assign Across Arms`, `Retire`.
>    - `/assessments` (Approvals queue): `Open Score Grid`, `Approve Scores`, `Unlock Sheet (with Reason)`.
>    - `/results`: `Edit Remarks`, `Preview Report Card` (`/results/verify/demo`), `Copy Result Link` (when published).
>
> 1. **Staff, Permissions & Account (`/staff`, `/settings`):**
>    - `/staff`: `Invite Staff` drawer (`email`, `first_name`, `last_name`, `phone_number`, `role`, `staff_id_number`, `specialization`) + `Bulk Upload Staff` tab/drawer (client-side template download + CSV/Excel dropzone + job polling progress + row error table).
>    - `Deactivate Staff` confirmation dialog (warns that active sessions, Class Teacher, and Subject Teacher assignments will be revoked immediately).
>    - Inside `Manage Permissions` drawer: display **existing scoped grants with a Revoke button** alongside the form to add a new grant (`Whole School`, `Department`, `Level`, or `Specific Class Arm`).
>    - `/settings`: Complete `School Profile & Branding` (Logo, **Official School Stamp**, and Principal/Head Teacher Signature uploads) + `My Account & Security` tab (`Edit Profile` form + `Change Password` form).
> 2. **Classes & Terms (`/structure` & `/structure/classes/:id`):**
>    - Create/Edit drawers + Retire confirmation dialogs for **Sessions** (`Set as Current`), **Terms** (dates, `Set as Current`, and **`Lock / Unlock Scoring`** toggle), **Levels**, **Departments**, **Arm Letters** (`O, L, I, V` / `A–D`), and **Class Arms / Cohorts** (`SS1 O`, `Basic 1 A`).
>    - `Assign / Change Class Teacher` modal on both `/structure` and `/structure/classes/:id`, plus `Add Subject to Class`, `Remove / Retire Subject from Class`, `Bulk Register Students into Elective`, and `Assign / Unassign Subject Teacher` drawers on `/structure/classes/:id`.
> 3. **Students (`/students` & `/students/:id`):**
>    - `Add / Edit Student` drawer (including Parent/Guardian info), `Enroll into Class Arm` modal, `Promote / Transfer / Withdraw Enrollment` modal, and `Change Student Status` confirm modal.
>    - `Bulk Upload (Excel)` tab: client-side `Download Sample Template` button, dropzone, job polling progress, and row-level error summary.
>    - On `/students/:id`: `Manage Elective / Trade Subjects` drawer (add/drop electives for Senior Secondary) + **Student Daily Attendance History** tab/list (`Present` / `Absent` / `Late` log).
> 4. **Subjects (`/subjects`):**
>    - `Add / Edit / Retire Subject` modal (`Core` / `Elective` / `Trade`), `Add / Edit / Retire Level Offering` modal, and `Bulk Assign Teacher Across Arms` modal (assign one teacher to multiple arms at once).
> 5. **Attendance, Scores & Results Workflows (`/attendance`, `/assessments`, `/results`):**
>    - `/attendance`: Support `Present` / `Absent` / **`Late`** segmented options + auto-reveal `Backdate Reason` input when selected date is in the past + `Manual Attendance Override` modal (`Times Opened`, `Present`, `Absent`, required `Override Reason`, live `Present + Absent = Opened` check).
>    - `/assessments`: `Add / Edit CA & Exam Component` modal, `Unlock Score Sheet` modal (with required `Reason`), `Submit / Approve` confirm dialogs, and an error modal/banner listing missing students if submission fails.
>    - `/results`: `Publish Term` confirmation modal (warns of irreversible freeze) + **One-Time Result Links warning banner** (`Download CSV now` before leaving) + `Send Result Emails` confirmation modal + **Published / Locked Read-Only banner** on `/assessments`, `/attendance`, and `/results?tab=remarks`.
> 6. **Public QR Verify & Mobile Responsive Views (390px):** Add `/results/verify-qr` (public QR authenticity lookup page verifying HMAC payload) and ensure clean 390px mobile layouts for **Parent Report Card (`/results/verify/:token`)** (DOB gate + responsive printable card) and **Daily Attendance Roll Call (`/attendance`)**.

---

### Prompt 5: Routing Fixes, Login Illustration, Grouped Sidebar, Attendance Date UX & Activity Log
> **Keep all existing pages, modals, kebab menus, and Deep Petrol (`#0F4C5C`) / Warm Paper (`#FAF9F6`) styles intact—do NOT overwrite or remove any existing pages. Fix these navigation, UX, and visual gaps across the app:**
>
> 1. **Root Route (`/` → `/login`):** Make `/` render the Sign In page (`/login`) by default (when unauthenticated) since there is no public landing page yet.
> 2. **Login Page Illustration (`/login`):** On the `#0F4C5C` right-hand panel, right above the quote *"Olivet gives us one reliable record for every score, attendance mark, and result."* (`Mrs. Grace Oladipo`), add a crisp, bespoke **inline SVG illustration** (warm paper `#FAF9F6` and soft ochre `#FEF3C7` line-art) depicting Nigerian school academic records—a report card sheet, attendance checklist, and academic seal—so the upper half of the panel feels balanced and intentional.
> 3. **Grouped Left Sidebar Navigation:** Reorganize the sidebar into 4 clean groups with subtle section labels:
>    - **Overview:** `Home` (`/dashboard`), `Term Issues` (`/dashboard/issues`)
>    - **Academics:** `Classes & Terms` (`/structure`), `Students` (`/students`), `Subjects` (`/subjects`)
>    - **Term Operations:** `Attendance` (`/attendance`), `Scores & CA` (`/assessments`), `Results & Broadsheets` (`/results`)
>    - **Administration:** `Staff & Roles` (`/staff`), `Setup Wizard` (`/setup`), `Activity Log` (`/audit-logs`), `School Settings` (`/settings`)
> 4. **Fix Dead Dashboard & Setup Links:**
>    - On **Admin Dashboard (`/dashboard`)**: wire **`View term setup`** → `/setup` (or `/structure?tab=sessions-terms`), wire **`View all term issues`** → `/dashboard/issues`, and wire **`View activity log`** → new **`/audit-logs`** page.
>    - Add **`/audit-logs` (Activity Log Page):** Filterable by `Action` and `Resource Type`, showing timestamp, actor name, action badge, resource summary, and details drawer (backed by `GET /api/v1/audit/logs`).
>    - On **Guided Setup Wizard (`/setup`)**: fix the **School Template `Edit` button** so it opens **Step 3 (`Create Classes from Template`)** / the structure template selector instead of wrongly routing to `CA & Grading Rules`.
> 5. **Upgrade Attendance Date UX (`/attendance`):**
>    - Display a clear, prominent **Date Context Bar** at the top of `Daily Roll Call` showing the exact date being marked (e.g., `"Taking attendance for: Thursday, 19 Nov 2026 (Today)"`) with a native calendar date picker, `Today` / `Yesterday` quick chips, and `< Prev` / `Next >` day buttons (disable future dates).
>    - Whenever a **past date** is selected, automatically show an amber **Backdated Attendance** banner with the required `Backdate Reason` input right above `Save Roll Call`.

---

### Prompt 6: Modern Metric Cards Upgrade & Role-Correct Attendance UX
> **Keep all existing pages, routes, modals, and Deep Petrol (`#0F4C5C`) / Warm Paper (`#FAF9F6`) design system intact. Upgrade these 2 areas across the app:**
>
> 1. **Upgrade All Metric / KPI Cards (Dashboards & Operational Pages):**
>    - Replace the plain flat white boxes with **modern, high-craft SaaS metric cards** (clean `rounded-xl`, `20px` padding, `1px solid #E2E8F0` border, subtle `#0F4C5C/40` hover border on clickable cards—no hover lift/translate, zero heavy shadows or neon gradients):
>      - Give every metric card a **tinted `36px` icon badge** in the top corner (`#0F4C5C/10` for general metrics, `#FEF3C7` amber for pending/action items, `#DCFCE7` green for completed/approved).
>      - Where a metric represents a ratio or percentage (e.g., `Score Sheets Approved: 28 / 36`, `Classes Marked Today: 21 / 24`, `Present Today: 94.2%`), include a **compact status/breakdown pill** inside the card (no progress bars).
>      - On **Teacher Dashboard**, elevate the **`My Class: SS1 O`** card into a distinctive featured card (subtle `#F0F7F8` surface or `#0F4C5C` accent) showing `Science • 42 Students`, a live `Today's Attendance: Not Marked` status pill, and quick-action buttons (`Mark Attendance`, `Write Remarks`).
>
> 2. **Fix Attendance Role Separation & Manual Override UX (`/attendance`):**
>    - **Role Separation (Admin vs. Class Teacher):**
>      - **Admin view** does **NOT** take daily roll call for classes. In Admin view, `/attendance` should show a **School Attendance Overview** (which classes have marked today vs pending, school-wide attendance %, and read-only class summaries)—hide the `Daily Roll Call` marking form in Admin view.
>      - **Teacher view (Class Teacher):** Since a Class Teacher is assigned to **one single class arm** (`SS1 O`), remove the `Class Arm` dropdown in Teacher view and lock the header directly to their assigned class (`SS1 O • 42 Students`).
>    - **Fix `Manual override` Placement:**
>      - **Remove** the `Manual override` link from the `Daily Roll Call` tab (Daily Roll Call is strictly for `Present` / `Late` / `Absent` + `Mark all present` + `Save Roll Call`).
>      - Move `Edit Term Totals (Manual Override)` exclusively to the **`Term Attendance Summary` tab** inside each student row's `⋮` kebab menu.
>      - In the `Edit Term Totals` modal, clearly display **which student is being updated** (e.g., `"Correct Term Totals — Chinedu Okafor (OBH/2024/018)"`), with `Times School Opened`, `Times Present`, `Times Absent`, live `Present + Absent = Opened` validation, and `Override Reason`.
