import http from "k6/http";
import { sleep } from "k6";
import { API_PREFIX } from "../config.js";
import { getAuthHeaders } from "../lib/auth.js";
import { checkSuccess, checkTenantStudents } from "../lib/checks.js";

function pickRandom(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

export function executeDailyOps(manifest) {
  const school = pickRandom(manifest.schools);
  const teacher = pickRandom(school.teachers);
  const headers = getAuthHeaders(teacher.email, teacher.password);
  if (!headers) {
    sleep(1);
    return;
  }

  const roll = Math.random();

  if (roll < 0.45) {
    // Read-heavy classroom & directory browsing
    const dashRes = http.get(`${API_PREFIX}/structure/dashboard/teacher`, {
      headers,
      tags: { endpoint: "teacher_dashboard", group: "reads" },
    });
    checkSuccess(dashRes, "teacher_dashboard");

    const adminHeaders = getAuthHeaders(school.admin.email, school.admin.password);
    if (adminHeaders) {
      const studentsRes = http.get(
        `${API_PREFIX}/students?page=1&page_size=50&q=A`,
        {
          headers: adminHeaders,
          tags: { endpoint: "list_students", group: "reads" },
        }
      );
      checkSuccess(studentsRes, "list_students");
      checkTenantStudents(studentsRes, school.school_id, school.admission_prefix);
    }

    const ttRes = http.get(
      `${API_PREFIX}/timetable/schedules/${school.timetable_id}/teachers/me`,
      {
        headers,
        tags: { endpoint: "my_timetable", group: "reads" },
      }
    );
    checkSuccess(ttRes, "my_timetable");
  } else if (roll < 0.75) {
    // Score sheet read + bulk score entry (use DRAFT class_subjects from cg[2..3])
    const draftCg = school.class_groups[2] || school.class_groups[0];
    const draftCsIds = teacher.class_subject_ids.filter((id) =>
      (school.draft_class_subject_ids || draftCg.class_subject_ids).includes(id)
    );
    const csId =
      draftCsIds.length > 0
        ? pickRandom(draftCsIds)
        : pickRandom(school.draft_class_subject_ids || draftCg.class_subject_ids);
    const defns = school.assessment_definitions[csId] || [];
    const defnId = defns.length > 0 ? defns[0] : pickRandom(school.assessment_definition_ids);

    // Use admin headers if teacher isn't assigned to this fallback draft csId
    const entryHeaders = draftCsIds.length > 0 ? headers : getAuthHeaders(school.admin.email, school.admin.password);
    if (!entryHeaders) {
      sleep(0.5);
      return;
    }

    const sheetRes = http.get(
      `${API_PREFIX}/assessments/scores?class_subject_id=${csId}&assessment_definition_id=${defnId}`,
      {
        headers: entryHeaders,
        tags: { endpoint: "get_score_sheet", group: "reads" },
      }
    );
    checkSuccess(sheetRes, "get_score_sheet");

    const cg = school.class_groups.find((g) => g.class_subject_ids.includes(csId));
    const enrollmentIds = cg ? cg.enrollment_ids : [];
    if (enrollmentIds.length > 0) {
      const payload = {
        class_subject_id: csId,
        assessment_definition_id: defnId,
        scores: enrollmentIds.map((eid) => ({
          student_enrollment_id: eid,
          score_status: "SCORED",
          raw_score: Number((5 + Math.random() * 5).toFixed(1)),
        })),
      };
      const entryRes = http.post(
        `${API_PREFIX}/assessments/scores/bulk-entry`,
        JSON.stringify(payload),
        {
          headers: entryHeaders,
          tags: { endpoint: "bulk_score_entry", group: "bulk_writes" },
        }
      );
      checkSuccess(entryRes, "bulk_score_entry");
    }
  } else if (roll < 0.95) {
    // Daily attendance read + mark (form master owns their class_group)
    const cgId = teacher.form_class_group_id || school.class_groups[0].id;
    const cg = school.class_groups.find((g) => g.id === cgId) || school.class_groups[0];

    const getAtt = http.get(
      `${API_PREFIX}/structure/class-groups/${cg.id}/attendance?academic_term_id=${school.active_term_id}&attendance_date=2026-11-24`,
      {
        headers,
        tags: { endpoint: "get_class_attendance", group: "reads" },
      }
    );
    checkSuccess(getAtt, "get_class_attendance");

    const markPayload = {
      academic_term_id: school.active_term_id,
      attendance_date: "2026-11-24",
      reason: "Load test register update",
      records: cg.enrollment_ids.map((eid) => ({
        student_enrollment_id: eid,
        status: Math.random() < 0.92 ? "PRESENT" : "ABSENT",
      })),
    };
    const postAtt = http.post(
      `${API_PREFIX}/structure/class-groups/${cg.id}/attendance`,
      JSON.stringify(markPayload),
      {
        headers,
        tags: { endpoint: "mark_class_attendance", group: "bulk_writes" },
      }
    );
    checkSuccess(postAtt, "mark_class_attendance");
  } else {
    const meRes = http.get(`${API_PREFIX}/identity/me`, {
      headers,
      tags: { endpoint: "identity_me", group: "reads" },
    });
    checkSuccess(meRes, "identity_me");
  }

  sleep(0.5 + Math.random());
}
