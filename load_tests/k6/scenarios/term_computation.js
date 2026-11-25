import http from "k6/http";
import { sleep } from "k6";
import { API_PREFIX } from "../config.js";
import { getAuthHeaders } from "../lib/auth.js";
import { checkSuccess } from "../lib/checks.js";

function pickRandom(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

export function executeTermComputation(manifest) {
  const school = pickRandom(manifest.schools);
  const headers = getAuthHeaders(school.admin.email, school.admin.password);
  if (!headers) {
    sleep(1);
    return;
  }

  const approvedCgIds = school.approved_class_group_ids || [school.class_groups[0].id];
  const cgId = pickRandom(approvedCgIds);

  // 1. Admin dashboard & score sheet overview
  const overviewRes = http.get(
    `${API_PREFIX}/assessments/sheets?academic_term_id=${school.active_term_id}&page=1&page_size=50`,
    {
      headers,
      tags: { endpoint: "score_sheets_overview", group: "reads" },
    }
  );
  checkSuccess(overviewRes, "score_sheets_overview");

  // 2. Broadsheet read (recomputes from raw scores in memory)
  const broadsheetRes = http.get(
    `${API_PREFIX}/computation/broadsheet/${cgId}?academic_term_id=${school.active_term_id}&page=1&page_size=50`,
    {
      headers,
      tags: { endpoint: "get_broadsheet", group: "heavy_compute" },
    }
  );
  checkSuccess(broadsheetRes, "get_broadsheet");

  // 3. Synchronous calculate-term for an approved class group
  const calcPayload = {
    academic_term_id: school.active_term_id,
    class_group_id: cgId,
  };
  const calcRes = http.post(
    `${API_PREFIX}/computation/calculate-term`,
    JSON.stringify(calcPayload),
    {
      headers,
      tags: { endpoint: "calculate_term", group: "heavy_compute" },
    }
  );
  checkSuccess(calcRes, "calculate_term");

  sleep(1 + Math.random());
}
