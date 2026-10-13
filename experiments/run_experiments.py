"""Reproducible local robustness and report experiments for Lab 25B."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gameday_lab.cache import CachingClient, SemanticCache
from gameday_lab.client import ResilientClient
from gameday_lab.factory import build_client
from gameday_lab.sim.config import LabConfig, load_config
from gameday_lab.sim.engine import evaluate_checks, run_scenario

OUT = Path("reports/experiments")


def merge_config(cfg: LabConfig, client: dict[str, Any] | None = None,
                 cache: dict[str, Any] | None = None) -> LabConfig:
    client_cfg = dict(cfg.client)
    if client:
        for key, value in client.items():
            if key == "breaker":
                client_cfg[key] = {**client_cfg.get(key, {}), **value}
            else:
                client_cfg[key] = value
    cache_cfg = {**cfg.cache, **(cache or {})}
    return replace(cfg, client=client_cfg, cache=cache_cfg)


def measure(cfg: LabConfig, scenario: str, client_name: str, seed: int,
            no_tool_filter: bool = False) -> dict[str, Any]:
    if no_tool_filter:
        class UnfilteredClient(ResilientClient):
            def ladder(self, task: str) -> list[str]:
                return list(self.order)

        client = UnfilteredClient(cfg.provider_infos(), cfg.fallback_order, cfg.client, seed=1)
    else:
        client = build_client(client_name, cfg, seed=1)
    sim = run_scenario(cfg, scenario, client, seed)
    metrics = sim.metrics()
    checks = evaluate_checks(metrics, cfg.scenarios[scenario].checks)
    return {**metrics, "checks": checks, "pass": all(checks.values())}


def show(label: str, scenario: str, seed: int, result: dict[str, Any]) -> None:
    failed = [key for key, ok in result["checks"].items() if not ok]
    fields = [key for key in result["checks"] if key.replace("_min", "").replace("_max", "") in result]
    metrics = " ".join(f"{key}={result.get(key.replace('_min', '').replace('_max', ''))}" for key in fields)
    print(f"[{label}] {scenario} seed={seed} {'PASS' if result['pass'] else 'FAIL'} {metrics} {','.join(failed)}")


def robust() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    standard = load_config("configs/gameday.yaml", "configs/scenarios.yaml")
    variants = load_config("configs/gameday.yaml", "experiments/scenarios_variants.yaml")
    cache_standard = load_config("configs/gameday.yaml", "configs/scenarios_cache.yaml")
    cache_variants = load_config("configs/gameday.yaml", "experiments/scenarios_cache_variants.yaml")
    results: dict[str, Any] = {}
    for label, cfg, names, seeds, client_name in (
        ("C", standard, list(standard.scenarios), range(25, 30), "resilient"),
        ("V", variants, list(variants.scenarios), range(25, 28), "resilient"),
        ("G", cache_standard, list(cache_standard.scenarios), range(25, 30), "cached"),
        ("VC", cache_variants, list(cache_variants.scenarios), range(25, 28), "cached"),
    ):
        for name in names:
            for seed in seeds:
                result = measure(cfg, name, client_name, seed)
                results.setdefault(name, {})[str(seed)] = result
                show(label, name, seed, result)
    (OUT / "robustness.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


def report() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    game = load_config("configs/gameday.yaml", "configs/scenarios.yaml")
    cache = load_config("configs/gameday.yaml", "configs/scenarios_cache.yaml")
    results: dict[str, Any] = {}

    def record(name: str, cfg: LabConfig, client_name: str, scenarios: list[str], *, unfiltered: bool = False) -> None:
        results[name] = {scenario: measure(cfg, scenario, client_name, 25, unfiltered) for scenario in scenarios}
        for scenario, value in results[name].items():
            show(name, scenario, 25, value)

    record("base", game, "resilient", ["G2_overload_storm", "G3_brownout", "G4_slow_region", "G6_provider_outage"])
    budget_broken = merge_config(game, {"breaker": {"min_calls": 1_000_000_000}, "max_attempts_per_provider": 5})
    record("exp1a_budget_on", budget_broken, "resilient", ["G2_overload_storm", "G3_brownout"])
    record("exp1b_budget_off", merge_config(budget_broken, {"retry_budget_ratio": 100,
           "retry_budget_max_tokens": 1.0e9, "retry_budget_min_per_s": 1.0e6}), "resilient",
           ["G2_overload_storm", "G3_brownout"])
    record("exp2_timeout_30", merge_config(game, {"attempt_timeout_s": 30}), "resilient",
           ["G4_slow_region", "G6_provider_outage"])
    record("exp3_no_tool_filter", game, "resilient", ["G6_provider_outage"], unfiltered=True)
    record("cache_base", cache, "cached", ["C1_faq_outage"])
    for threshold in (0.80, 0.97):
        record(f"cache_thr_{threshold:.2f}", merge_config(cache, cache={"similarity_threshold": threshold}),
               "cached", ["C1_faq_outage"])
    for ttl in (30, 60, 300):
        record(f"cache_ttl_{ttl}", merge_config(cache, cache={"ttl_s": ttl}), "cached", ["C1_faq_outage"])
    record("cache_stale_0", merge_config(cache, cache={"max_stale_s": 0}), "cached", ["C1_faq_outage"])
    (OUT / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("robust", "report"))
    args = parser.parse_args()
    robust() if args.mode == "robust" else report()


if __name__ == "__main__":
    main()
