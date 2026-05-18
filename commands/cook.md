---
description: Cook a rough idea or problem into a sharp design — adversarial interview, alternatives surfacing, structured brief.
argument-hint: "[idea, problem, or path-to-doc]"
allowed-tools: Read, Grep, Glob, AskUserQuestion, Agent
---

Interview the user relentlessly about every aspect of their plan until you reach a shared understanding. Walk down each branch of the design tree, resolving dependencies between decisions one-by-one. Surface distinct approaches with tradeoffs when multiple paths are plausible, ground load-bearing claims with the `researcher` agent, then output a structured brief.

- If `$ARGUMENTS` is provided, that's the input (Read it if it's a file path, otherwise treat it as the idea/problem).
- If `$ARGUMENTS` is empty, use prior conversation context.
- If there's no usable context, ask: "what do you want cooked?"

Rules:
- **Check scope first.** If the request describes multiple independent subsystems, flag and decompose before designing.
- **One question at a time** via AskUserQuestion. 2–4 multiple-choice options, recommended first labeled "(Recommended)". **Do NOT use `preview` fields on options**.
- **If a question can be answered by reading the codebase, read the codebase.** Don't ask.
- **Don't accept vague answers.** Reflect them back with sharper options.
- **Chase tension.** Drop the planned thread when an answer reveals contradiction, avoidance, or hidden assumption.
- **Ask uncomfortable questions.** Push toward what the user is avoiding.
- **Probe the negative space.** Ask about what's NOT mentioned — deliberate omission, or unconsidered?
- **Convert risks into questions.** Every risk you surface becomes a concrete follow-up question, not just a noted concern.
- **Surface alternatives when multiple paths are plausible.** Present 2–3 genuinely distinct approaches with tradeoffs, then recommend one.
- **Ground load-bearing assumptions.** Dispatch the `researcher` subagent and cite its returned Sources inline. Run in the background when the answer isn't blocking the next question.
- **YAGNI.** Cut features that don't earn their place.

## Output the brief

When the interview ends, output the brief as a markdown block in your response. Do not write a file.

Title it `# Claude Cooked 🔥`. Use these sections, in order:

1. **Problem** — the problem being addressed and what triggered it.
2. **Recommendation** — the proposed approach and why.
3. **Alternatives** *(if applicable)* — other paths considered, with the tradeoff that ruled each out.
4. **Risks** — what could break this.
