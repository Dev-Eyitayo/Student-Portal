export const BASE_URL = (__ENV.BASE_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");
export const API_PREFIX = `${BASE_URL}/api/v1`;
export const PROFILE = (__ENV.PROFILE || "smoke").toLowerCase();
export const SINGLE_SCENARIO = (__ENV.SCENARIO || "").toLowerCase();

export const THRESHOLDS = {
  "http_req_failed{endpoint:verify_qr}": ["rate<0.005"],
  "http_req_failed{group:auth}": ["rate<0.005"],
  "http_req_failed{group:reads}": ["rate<0.005"],
  "http_req_failed{group:bulk_writes}": ["rate<0.005"],
  "http_req_failed{group:heavy_compute}": ["rate<0.005"],
  "http_req_duration{endpoint:login}": ["p(95)<600", "p(99)<1200"],
  "http_req_duration{group:reads}": ["p(95)<300", "p(99)<600"],
  "http_req_duration{group:bulk_writes}": ["p(95)<800", "p(99)<1500"],
  "http_req_duration{group:heavy_compute}": ["p(95)<2500", "p(99)<5000"],
  "http_req_duration{group:public_verify}": ["p(95)<200", "p(99)<400"],
};

export function buildScenarios() {
  if (SINGLE_SCENARIO) {
    const singleMap = {
      daily_ops: {
        executor: "ramping-vus",
        exec: "runDailyOps",
        startVUs: 1,
        stages: [
          { duration: "30s", target: 10 },
          { duration: "2m", target: 25 },
          { duration: "30s", target: 0 },
        ],
      },
      term_computation: {
        executor: "ramping-vus",
        exec: "runTermComputation",
        startVUs: 1,
        stages: [
          { duration: "30s", target: 5 },
          { duration: "2m", target: 15 },
          { duration: "30s", target: 0 },
        ],
      },
      result_day_spike: {
        executor: "ramping-arrival-rate",
        exec: "runResultDaySpike",
        startRate: 5,
        timeUnit: "1s",
        preAllocatedVUs: 20,
        maxVUs: 150,
        stages: [
          { duration: "30s", target: 25 },
          { duration: "2m", target: 100 },
          { duration: "30s", target: 0 },
        ],
      },
      background_jobs: {
        executor: "per-vu-iterations",
        exec: "runBackgroundJobs",
        vus: 3,
        iterations: 3,
        maxDuration: "3m",
      },
      login_storm: {
        executor: "constant-arrival-rate",
        exec: "runLoginStorm",
        rate: 25,
        timeUnit: "1s",
        duration: "2m",
        preAllocatedVUs: 30,
        maxVUs: 100,
      },
    };
    const chosen = singleMap[SINGLE_SCENARIO];
    if (!chosen) {
      throw new Error(`Unknown SCENARIO=${SINGLE_SCENARIO}`);
    }
    return { [SINGLE_SCENARIO]: chosen };
  }

  switch (PROFILE) {
    case "smoke":
      return {
        daily_ops: { executor: "constant-vus", exec: "runDailyOps", vus: 3, duration: "1m" },
        term_computation: { executor: "constant-vus", exec: "runTermComputation", vus: 1, duration: "1m" },
        result_day_spike: { executor: "constant-vus", exec: "runResultDaySpike", vus: 2, duration: "1m" },
      };

    case "baseline":
      return {
        daily_ops: {
          executor: "ramping-vus",
          exec: "runDailyOps",
          startVUs: 5,
          stages: [
            { duration: "2m", target: 50 },
            { duration: "11m", target: 50 },
            { duration: "2m", target: 0 },
          ],
        },
      };

    case "spike":
      return {
        daily_ops: {
          executor: "constant-vus",
          exec: "runDailyOps",
          vus: 30,
          duration: "12m",
        },
        term_computation: {
          executor: "ramping-vus",
          exec: "runTermComputation",
          startVUs: 0,
          stages: [
            { duration: "2m", target: 20 },
            { duration: "8m", target: 20 },
            { duration: "2m", target: 0 },
          ],
        },
        result_day_spike: {
          executor: "ramping-arrival-rate",
          exec: "runResultDaySpike",
          startRate: 10,
          timeUnit: "1s",
          preAllocatedVUs: 50,
          maxVUs: 350,
          stages: [
            { duration: "1m", target: 150 },
            { duration: "5m", target: 150 },
            { duration: "1m", target: 10 },
          ],
        },
      };

    case "stress":
      return {
        daily_ops: {
          executor: "ramping-arrival-rate",
          exec: "runDailyOps",
          startRate: 25,
          timeUnit: "1s",
          preAllocatedVUs: 50,
          maxVUs: 400,
          stages: [
            { duration: "3m", target: 50 },
            { duration: "3m", target: 100 },
            { duration: "3m", target: 150 },
            { duration: "3m", target: 200 },
            { duration: "3m", target: 250 },
            { duration: "3m", target: 300 },
          ],
        },
      };

    case "soak":
      return {
        daily_ops: {
          executor: "constant-vus",
          exec: "runDailyOps",
          vus: 35,
          duration: "60m",
        },
        result_day_spike: {
          executor: "constant-arrival-rate",
          exec: "runResultDaySpike",
          rate: 20,
          timeUnit: "1s",
          duration: "60m",
          preAllocatedVUs: 20,
          maxVUs: 80,
        },
      };

    default:
      throw new Error(`Unknown PROFILE=${PROFILE}. Use smoke|baseline|spike|stress|soak`);
  }
}
