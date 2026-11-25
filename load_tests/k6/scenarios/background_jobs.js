import http from "k6/http";
import { check, sleep } from "k6";
import { API_PREFIX } from "../config.js";
import { getAuthHeaders } from "../lib/auth.js";

function pickRandom(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

export function executeBackgroundJobs(manifest) {
  const school = pickRandom(manifest.schools);
  const headers = getAuthHeaders(school.admin.email, school.admin.password);
  if (!headers) {
    sleep(1);
    return;
  }

  // Trigger async timetable generation (202 Accepted -> Celery default queue)
  const genRes = http.post(
    `${API_PREFIX}/timetable/schedules/${school.timetable_id}/generate`,
    JSON.stringify({}),
    {
      headers,
      tags: { endpoint: "timetable_generate", group: "background" },
    }
  );
  check(genRes, {
    "timetable generate accepted or conflict": (r) =>
      r.status === 202 || r.status === 200 || r.status === 409,
  });

  const taskId = genRes.json("data.task_id");
  if (taskId) {
    for (let i = 0; i < 5; i++) {
      sleep(1);
      const pollRes = http.get(
        `${API_PREFIX}/timetable/schedules/${school.timetable_id}/jobs/${taskId}`,
        {
          headers,
          tags: { endpoint: "timetable_job_poll", group: "reads" },
        }
      );
      if (pollRes.status === 200 && pollRes.json("data.generation_status") !== "RUNNING") {
        break;
      }
    }
  }

  // Trigger term result notifications (202 Accepted -> Celery notifications queue)
  const notifyRes = http.post(
    `${API_PREFIX}/publication/notify-term`,
    JSON.stringify({
      academic_term_id: school.published_term_id,
      result_links: [
        {
          student_enrollment_id: school.class_groups[0].enrollment_ids[0],
          token: school.result_tokens[0].token,
        },
      ],
    }),
    {
      headers,
      tags: { endpoint: "notify_term", group: "background" },
    }
  );
  check(notifyRes, {
    "notify-term 202": (r) => r.status === 202,
  });

  sleep(2);
}
