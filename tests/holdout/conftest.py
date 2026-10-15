"""pytest wiring for the slm-eval holdout suite (contract v0.3).

Every test gets its own sandbox: a temporary dataset cache (SLM_EVAL_CACHE),
run store (SLM_EVAL_RUNS) and working directory. Tests that need real
resources carry a marker and are skipped unless the resource is enabled:

  requires_data     pinned upstream dataset: SLM_EVAL_HOLDOUT_SEED_CACHE or
                    SLM_EVAL_HOLDOUT_NETWORK=1
  requires_network  `slm-eval data fetch` from upstream: SLM_EVAL_HOLDOUT_NETWORK=1
  requires_runtime  real model artifact and pinned llama.cpp runtime:
                    SLM_EVAL_HOLDOUT_RUNTIME=1, plus SLM_EVAL_HOLDOUT_SEED_CACHE
                    holding models/ (§2: model files live in the dataset cache)

See _ho_support.py for the full list of configuration variables.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The suite lives on a volume whose files are registered by hash: never write
# bytecode next to it.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402

import _ho_support as hs  # noqa: E402


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "requires_data: needs the pinned upstream dataset (seed cache or network)")
    config.addinivalue_line("markers", "requires_network: needs network access to the pinned upstream")
    config.addinivalue_line("markers", "requires_runtime: needs the real model artifact and pinned runtime")


def pytest_runtest_setup(item: pytest.Item) -> None:
    has_data = hs.env_path("SEED_CACHE") is not None or hs.env_flag("NETWORK")
    if item.get_closest_marker("requires_network") and not hs.env_flag("NETWORK"):
        pytest.skip("requires network access: set SLM_EVAL_HOLDOUT_NETWORK=1")
    if item.get_closest_marker("requires_data") and not has_data:
        pytest.skip("requires the pinned dataset: set SLM_EVAL_HOLDOUT_SEED_CACHE or SLM_EVAL_HOLDOUT_NETWORK=1")
    if item.get_closest_marker("requires_runtime") and not (hs.env_flag("RUNTIME") and hs.env_path("SEED_CACHE")):
        pytest.skip("requires the real model and runtime: set SLM_EVAL_HOLDOUT_RUNTIME=1 and "
                    "SLM_EVAL_HOLDOUT_SEED_CACHE (with models/)")


@pytest.fixture
def sandbox(tmp_path: Path) -> hs.Sandbox:
    return hs.Sandbox(tmp_path / "sandbox")


@pytest.fixture
def ctx() -> hs.ProtocolContext:
    return hs.protocol_context()
