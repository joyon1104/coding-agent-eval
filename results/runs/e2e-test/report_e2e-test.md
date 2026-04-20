# Coding Agent Eval Report

- **Run ID**: e2e-test
- **Tier**: local
- **Tasks**: 5
- **Generated**: 2026-04-20 15:51:13

## Metric Comparison

| Metric | mock-claude-code | mock-codex |
|--------|--------|--------|
| task_resolution_rate | 80.0% (S) | 40.0% (B) |
| regression_safety | 100.0% (S) | 100.0% (S) |
| token_efficiency | 106805.8 (B) | 82530.0 (A) |
| cost_per_resolved_task | $0.706 (A) | $0.730 (A) |
| e2e_time | 153.9s (B) | 121.4s (B) |
| time_to_first_action | 5.2s (B) | 2.8s (S) |
| convergence_steps | 22.2 (C) | 21.6 (C) |

## Grade Legend

S = Excellent | A = Good | B = Average | C = Below Average | D = Poor | F = Failing