import http from "k6/http";
import { check } from "k6";
import { API_PREFIX } from "../config.js";

function pickRandom(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

export function executeLoginStorm(manifest) {
  const school = pickRandom(manifest.schools);
  const teacher = pickRandom(school.teachers);

  const res = http.post(
    `${API_PREFIX}/identity/login`,
    JSON.stringify({ email: teacher.email, password: teacher.password }),
    {
      headers: { "Content-Type": "application/json" },
      tags: { endpoint: "login", group: "auth" },
    }
  );

  check(res, {
    "login_storm status 200": (r) => r.status === 200,
    "login_storm has token": (r) => Boolean(r.json("data.access_token")),
  });
}
