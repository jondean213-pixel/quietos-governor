# QuietOS — The Governor

**The brain proposes, QuietOS disposes.**

QuietOS is a brain-agnostic runtime governor for AI agents. It sits between any AI model and its tools, enforcing safety gates on every action — regardless of which model is thinking.

## Why This Exists

AI is getting more capable and more autonomous. But the deeper fears aren't about what AI *is* — they're about what we're losing:

- **Loss of agency.** Every AI layer is a place where a human used to decide.
- **Black-box opacity.** Even the builders can't fully explain why models do what they do.
- **Indifference, not malice.** The danger isn't a robot that hates you — it's one optimizing for something while you're not in the equation.
- **Power concentration.** The most capable AI lives inside a handful of companies.
- **Reality erosion.** When anything can be faked, shared truth dissolves.

QuietOS is a direct answer to the first three. It doesn't try to make the brain smarter or safer — it makes sure the brain can't act without permission.

## How It Works

The governor enforces three authority tiers on every proposed action:

| Tier | Meaning | Example |
|------|---------|---------|
| **ALLOW** | Safe, read-only, reversible — executes immediately | Reading files, web search, drafting |
| **ESCALATE** | Needs explicit human approval | Writing files, sending messages |
| **BLOCK** | Never permitted | Destructive commands, unauthorized writes |

Gates are **deterministic code** — allowlists, exact-match flags, pattern checks. No LLM in the enforcement loop. No probabilistic guessing. The gate doesn't care how clever the prompt was; it cares what the brain tries to *do*.

**Brain-agnostic by design, proven by swap.** The same gates held under Groq's 120B model and Google's Gemini — including under active adversarial attack. Swap the brain, keep the governor.

## The Proof

We don't ask you to trust us. We attack our own governor and publish the results.

**Red-team record (Oct 14–15, 2026):**
- 26 adversarial attacks tested
- 15 blocked outright
- 11 real holes found — **all patched and verified same session**
- 3 control tests passed (legitimate work unbroken)
- Zero bypasses standing

Every hole we find gets fixed, retested, and logged with a date. The [Adversarial Proof Log](docs/PROOF-LOG.md) is the backbone of this project — and our sales deck.

## Quick Start

```bash
pip install quietos
```

```python
from quietos import Governor

gov = Governor()

# The brain proposes...
action = brain.decide("delete all temp files")

# QuietOS disposes.
result = gov.evaluate(action)
# → BLOCKED: destructive action without ALLOW_DESTRUCTIVE
```

*(Packaging in progress — check back soon.)*

## The Story

QuietOS was built by one person on a 4GB RAM phone, for about $115 in total investment. No lab, no funding, no team. Just a library chair and the conviction that AI needs a governor.

If it can be built on that, it can run on anything.

## Tiers

- **Free / Open Source** — the core governor, MIT licensed. Everyone deserves peace of mind.
- **Small Business ($29/mo)** — for teams running AI agents. Audit logs, priority updates.
- **Professional ($199/mo)** — approval workflows, compliance reporting, dedicated support.
- **Enterprise (custom)** — SLA, dedicated infrastructure, custom integrations.

## Contributing

We welcome adversarial thinking. Found a bypass? Open an issue — if it's real, we'll fix it, credit you, and log it in the proof record.

## License

MIT — see [LICENSE](LICENSE).

---

*Built by DeanWay Labs. The brain proposes, QuietOS disposes.*
