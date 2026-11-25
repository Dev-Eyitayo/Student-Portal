import http from "k6/http";
import { check, sleep } from "k6";
import { API_PREFIX } from "../config.js";
import { getAuthHeaders } from "../lib/auth.js";
import { checkSuccess, checkTenantTranscript } from "../lib/checks.js";

function pickRandom(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

export function executeResultDaySpike(manifest) {
  const school = pickRandom(manifest.schools);
  const roll = Math.random();

  if (roll < 0.6) {
    // Public parent verification via ResultAccessToken (writes access_count on GET)
    const tokenItem = pickRandom(school.result_tokens);
    const verifyRes = http.get(
      `${API_PREFIX}/results/verify/${tokenItem.token}?admission_number=${encodeURIComponent(
        tokenItem.admission_number
      )}`,
      {
        tags: { endpoint: "verify_result_token", group: "public_verify" },
      }
    );
    check(verifyRes, {
      "verify_result_token 200 or 429": (r) => r.status === 200 || r.status === 429,
    });
  } else if (roll < 0.8) {
    // Public stateless HMAC QR verification
    const tokenItem = pickRandom(school.result_tokens);
    const qrRes = http.get(
      `${API_PREFIX}/results/verify-qr?payload=${encodeURIComponent(tokenItem.qr_payload)}`,
      {
        tags: { endpoint: "verify_qr", group: "public_verify" },
      }
    );
    checkSuccess(qrRes, "verify_qr");
  } else {
    // Authenticated admin/teacher reading published report cards & multi-term transcripts
    const headers = getAuthHeaders(school.admin.email, school.admin.password);
    if (!headers) {
      sleep(0.5);
      return;
    }

    const cardsRes = http.get(
      `${API_PREFIX}/publication/report-cards?academic_term_id=${school.published_term_id}&page=1&page_size=50`,
      {
        headers,
        tags: { endpoint: "list_report_cards", group: "reads" },
      }
    );
    checkSuccess(cardsRes, "list_report_cards");

    const studentId = pickRandom(school.student_ids);
    const transcriptRes = http.get(
      `${API_PREFIX}/publication/students/${studentId}/transcript`,
      {
        headers,
        tags: { endpoint: "get_student_transcript", group: "heavy_compute" },
      }
    );
    checkSuccess(transcriptRes, "get_student_transcript");
    checkTenantTranscript(transcriptRes, school.admission_prefix);
  }
}
