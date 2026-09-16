# Coding Conventions

Rules for writing code in this repo. Sibling conventions: [commit.md](./commit.md), [pr.md](./pr.md), [worktree.md](./worktree.md).

## Types

- **No escape-hatch types.** No `any` in TypeScript, no `Any` in Python. Use `unknown` (TS) or `object` (Python) at the boundary (parsed JSON, untyped APIs) and narrow with a type guard. If a third-party type is missing, write the minimal `interface` that captures what's actually used, not the whole library surface.
- **Avoid type assertions (`as Foo`) when a guard would do.** Casts hide bugs; guards surface them.

## Code

- **No comments that restate the code.** A `// increment counter` above `i++` is noise. Comments belong on *why*, not *what* — a hidden constraint, a subtle invariant, a workaround for a specific bug, behavior that would surprise a reader.
- **Don't write JSDoc/docstrings for self-evident functions.** Name the function and its parameters so the signature carries the meaning.
- **No dead code.** If something isn't called, delete it — don't leave it commented out "in case." Git remembers.
- **Don't add error handling, fallbacks, or validation for scenarios that can't happen.** Trust internal code and framework guarantees. Validate at system boundaries only (user input, external APIs, file I/O, network).
- **Don't introduce abstractions until they earn their place.** Three similar lines is not a duplication problem; it's three lines. Extract when the second use case is real, not when you imagine a third.

## Files & structure

- **One concept per file.** If a file does two unrelated things, split it.
- **Prefer editing an existing file to creating a new one.** Especially for tiny helpers — colocate them with their caller until they have more than one caller.
- **No new top-level directories without a reason.** This repo's layout is established; if you think you need a new top-level folder, surface that decision first.
