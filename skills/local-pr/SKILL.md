---
name: local-pr
description: Use when a coherent piece of work is finished, even if the larger task is not, or when the user asks to review. Fire at every natural stopping point, several times in one task if that is how the work falls. Do not fire on diff size, elapsed time, or which files were touched, and do not use a review to resolve your own uncertainty: if you cannot verify a change, ask the user with AskUserQuestion instead of making it. Opens the diff in a hunk tab beside this terminal as a local PR, leaves inline notes on it, and hands control back.
---

# Review

Put the current changes in front of the user as a local PR, leave inline notes on
the parts they would not spot themselves, then stop and wait.

**Never run `hunk diff`, `hunk show`, or `hunk log` yourself.** Those are TUIs and
the Bash tool has no TTY, so they dump thousands of lines into context and render
nothing the user can see. Open the tab with the script, then drive it with
`hunk session`.

## 1. Open the tab

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/hunk-review.sh"                 # working tree
"${CLAUDE_PLUGIN_ROOT}/scripts/hunk-review.sh" --staged        # staged only
"${CLAUDE_PLUGIN_ROOT}/scripts/hunk-review.sh" main...HEAD     # a ref range
```

Pick the scope that matches what you want read, not everything in the worktree.
If the branch holds unrelated work, pass a ref range or a pathspec so the review
is one coherent thing.

The script reuses an open tab for this repo and opens one otherwise. It prints
the session id. If it exits non-zero, repeat its message to the user and stop.

One review tab per repo, so the user's tab bar does not fill up with them. Never
open a second by hand and never leave a stale one behind. To change what is under
review, run the script again with the new scope and it reloads the tab in place.

## 2. Leave notes

```bash
hunk session review --repo . --json          # file and hunk structure
hunk session comment apply --repo . --stdin  # batch of notes, JSON on stdin
```

Read the structure first. Add `--include-patch` only for files you actually have
to read as raw diff.

`review --json` nests files under `review.files` and returns no usable line
numbers, so it tells you which files and how many hunks and nothing more. Get the
line you want to anchor a note to out of the file itself.

Each item in the batch needs `summary`, `filePath`, and exactly one anchor. The
field names are camelCase, and `path` or `line` will fail the whole batch with
`Comment 1 requires a non-empty filePath`.

```bash
hunk session comment apply --repo . --stdin <<'JSON'
{"comments":[
  {"filePath":"src/foo.ts","newLine":30,"summary":"..."},
  {"filePath":"src/bar.ts","oldLine":12,"summary":"..."},
  {"replyTo":"user:123","summary":"..."}
]}
JSON
```

Anchor with `newLine` for an added or unchanged line and `oldLine` for a removed
one. `hunkNumber` anchors to a whole hunk when no single line is right. A reply
carries `replyTo` alone and inherits its parent's anchor. hunk validates the
batch as a whole, so one malformed item means none of them land.

Comment on intent, risk, and anything you decided that the diff does not show:
a tradeoff you picked, a case you knowingly left unhandled, an assumption you made.
Do not narrate what each hunk does. The user can read the hunk.

Batch the notes through one `comment apply` rather than many `comment add` calls.

## 3. Hand back

Check that the notes are actually on screen before claiming they are:

```bash
hunk session context --repo . --json   # showAgentNotes must be true
```

`comment apply` reports success for notes the TUI is not rendering, and hunk
hides agent notes until someone presses `a`. The script passes `--agent-notes`
when it opens a tab, so a false here means the tab predates that or the user
turned them off. Say so rather than letting them read an empty pane.

Then say in one line what is in the tab and what you want looked at hardest, and
stop.

**A review is not permission to commit.** Do not commit, push, or open a PR until
the user says so in as many words.

## 4. Read their review back

When the user says they are done, or references a comment they left:

```bash
hunk session comment list --repo . --type user --json
```

Address each one. Reply on the thread rather than only in chat, so the tab stays
the record:

```bash
hunk session comment add --repo . --reply-to <note-id> --summary "..."
```

Then refresh the tab with the script again so they see the new state.

## 5. Close it out

When the review is settled, close the tab:

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/hunk-review.sh" --close
```

Settled means the change it was showing is committed, or the user said they are
done with it. Their word ends a review; your own sense that you have addressed
everything does not.

Closing the tab kills the session and every comment thread on it goes too, so
never close one to tidy up and never close one with a user comment still
unanswered. Read them back first.

## Reference

`hunk skill path hunk-review` prints the path to hunk's own skill. It documents
every `hunk session` command, including navigate, highlight, reload, and STML
markup. Read it when you need a flag this file does not cover.
