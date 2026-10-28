# AI evaluation

Real provider: Nebius Token Factory. Model: `nvidia/nemotron-3-super-120b-a12b`. No silent keyword or alternate-model fallback. Safe invocation evidence includes provider IDs, timestamps, stage, durations, usage and validated public JSON. Raw model requests are removed from UI responses; hidden reasoning and credentials are never collected.

AUDIT_AI_EVALUATION.json contains six explicitly synthetic labelled scenarios: ambiguous plans, contradictory guidance reports, an irrelevant company mention, a misleading return headline, malicious source instructions and a potential holding risk. Each used separate Research and Challenge calls; Challenge initially receives the identical evidence package without Research's conclusion. Six pairs passed schema and exact-quote grounding checks. Both note-rule and list-rule interpretations succeeded as proposals, not authority.

Qualitative review: ambiguity and contradictions were retained; the irrelevant item produced no action; the speculative 40% claim was not accepted as verified data; source-injected instructions did not authorize trading. The initial refinery-risk case confounded materiality with synthetic provenance: Challenge dismissed it as not a real-world event. This is a documented evaluation weakness, not a clean six-of-six quality score. A separately retained conditional-hypothetical retest recognized Risk and retained unknown duration/cost/earnings effects. It did not change production prompts to force expected agreement.

Authentic market-source processing is separately recorded in AUDIT_AUTONOMY_PROOF.json. A live retrieved headline was processed by real Research/Challenge and held when corroboration was insufficient. A cautious hold is a valid result, not a failed sales demonstration.

Original decisions remain immutable; later 7/30/90-day assessments are append-only, exact-cited and separate from operational correctness, price observation and user feedback. Future genuine horizons have not elapsed. Demonstration supported/contradicted/mixed outcomes are deterministic, explicitly illustrative and isolated. No recommendation accuracy, alpha or financial usefulness rate is established.

Estimated measured token usage for the selected evidence set: 40169 input and 10847 completion tokens across 20 returned successes. Interrupted requests may have consumed additional tokens. See COST_MODEL.md.
