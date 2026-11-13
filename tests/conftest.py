import os

# Set testing environment variables before any app modules load
os.environ["ENVIRONMENT"] = "testing"
os.environ["TESTING"] = "1"

import pytest
from hypothesis import settings, HealthCheck

# Optimize hypothesis settings for fast deterministic test suites
settings.register_profile(
    "fast",
    max_examples=25,
    deadline=1000,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture]
)
settings.load_profile("fast")
