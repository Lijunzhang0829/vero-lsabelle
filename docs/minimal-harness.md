# Minimal VERO Isabelle harness

Future minimal-baseline deployments select `python -m vero.isabelle.minimal`, not the historical generation runner. This entry reuses existing verify-environment, prepare, upgrade-slots, render, extract and evaluate operations; it does not expose run, plan-run or controller-dependent sampling. The host launches the official agent independently. No proof-search loop is introduced.

The agent-visible wrapper uses `python -m vero.isabelle.minimal_tool` (or imports tool.main with minimal=True). Only check, search and progress are exposed. A VERO_RETRY_SOCKET setting is rejected before input loading; validate-plan, result and controller feedback are unavailable. Progress explicitly describes candidate slot occupancy, not accepted proof counts. This wrapper can be mounted at the documented /backend/tool.py path.

The supported profile contains only seven common responsibilities: frozen input/environment, submission and clean rendering, native checking, trust audit and independent integration, legal fact query, external official-agent launch/resource limits, and read-only observation. Resource enforcement, deadline and usage collection remain external; this CLI is not itself a complete deployment. A future deployment must bind these services and the same native profile for all methods. Passive sampling must be controller-independent; existing PassiveSampler is not selected as-is.

Legacy modules remain for frozen historical runs and explicitly named optional variants; they are outside the minimal entry. Their presence does not enable planning, per-contract retries, helper budgets, custom SSE recovery, or forced session continuation. Do not relabel historical results as minimal-profile results.

Only fixed-implementation positive proofs and strict refutations are supported here. Codeproof, unsat and joint_unsat require additional benchmark interfaces. Tests remain local under the user's source-only publication policy. No frozen runtime or model experiment is changed by selecting this source entry.
