# Olivet Backend k6 Load Testing Suite

See [docs/load-testing-plan.md](../../docs/load-testing-plan.md) for the full architecture analysis, bottleneck targets, and SLO rationale.

## 1. Prerequisites

1. Install [k6](https://k6.io/docs/get-started/installation/).
2. Start your **staging** FastAPI server (`ENVIRONMENT=staging`, multi-worker Uvicorn, **not** `TESTING=1`) and Celery workers (`default,bulk_imports,notifications`).
3. Seed multi-tenant load-test data and generate `fixtures/seed_manifest.json`:
   ```bash
   .venv/bin/python -m scripts.seed_load_test --schools 5 --students-per-arm 25 --reset
   ```

## 2. Running Test Profiles

```bash
# 1. Smoke test (default)
k6 run -e BASE_URL=http://127.0.0.1:8000 load_tests/k6/main.js

# 2. Baseline (50 VUs, 15m)
k6 run -e BASE_URL=http://127.0.0.1:8000 -e PROFILE=baseline load_tests/k6/main.js

# 3. Term & Result-Day Spike
k6 run -e BASE_URL=http://127.0.0.1:8000 -e PROFILE=spike load_tests/k6/main.js

# 4. Stress / Breakpoint
k6 run -e BASE_URL=http://127.0.0.1:8000 -e PROFILE=stress load_tests/k6/main.js

# 5. Soak (60m)
k6 run -e BASE_URL=http://127.0.0.1:8000 -e PROFILE=soak load_tests/k6/main.js
```

## 3. Running Individual Scenarios

Set `SCENARIO` to isolate one workload (`daily_ops`, `term_computation`, `result_day_spike`, `background_jobs`, or `login_storm`):

```bash
k6 run -e BASE_URL=http://127.0.0.1:8000 -e SCENARIO=login_storm load_tests/k6/main.js
k6 run -e BASE_URL=http://127.0.0.1:8000 -e SCENARIO=term_computation load_tests/k6/main.js
```

## 4. Saving Results

```bash
mkdir -p load_tests/k6/results
k6 run --summary-export=load_tests/k6/results/baseline-summary.json \
  -e BASE_URL=http://127.0.0.1:8000 -e PROFILE=baseline load_tests/k6/main.js
```
