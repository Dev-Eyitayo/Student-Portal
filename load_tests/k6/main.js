import { SharedArray } from "k6/data";
import { THRESHOLDS, buildScenarios } from "./config.js";
import { executeDailyOps } from "./scenarios/daily_ops.js";
import { executeTermComputation } from "./scenarios/term_computation.js";
import { executeResultDaySpike } from "./scenarios/result_day_spike.js";
import { executeBackgroundJobs } from "./scenarios/background_jobs.js";
import { executeLoginStorm } from "./scenarios/login_storm.js";

const manifestData = new SharedArray("seed_manifest", function () {
  const raw = open("./fixtures/seed_manifest.json");
  return [JSON.parse(raw)];
});

export const options = {
  scenarios: buildScenarios(),
  thresholds: THRESHOLDS,
};

export function runDailyOps() {
  executeDailyOps(manifestData[0]);
}

export function runTermComputation() {
  executeTermComputation(manifestData[0]);
}

export function runResultDaySpike() {
  executeResultDaySpike(manifestData[0]);
}

export function runBackgroundJobs() {
  executeBackgroundJobs(manifestData[0]);
}

export function runLoginStorm() {
  executeLoginStorm(manifestData[0]);
}
