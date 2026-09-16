---
name: escalate
description: Use the moment work goes sideways or stalls. Something already discussed or drafted does not work, a fix keeps failing, what you find contradicts the plan, or the next step turns on something only the user knows. Also use before starting when the request has two defensible readings that lead to different work. Do not ask what you could check yourself, and do not sit on a real blocker or save it for an explanation at the end. Anything non-blocking waits for the next stopping point and goes in one batch. Turns it into an AskUserQuestion with plain scenarios and a recommendation first.
---

# Escalate

This is a signal, not a gate.

## Now, or at the next stop

Interrupt on the spot when the plan itself is in question: the agreed approach
does not work, the third attempt at the same fix failed, the code does not match
what you were told about it, or you are about to write something you have no way
to verify.

Everything else waits. Carry the open questions forward, keep making progress,
and put them in one AskUserQuestion when you reach a natural pause. Four prompts
in one task is three too many. One prompt holding four questions is fine.

If the behavior was already settled and only the implementation is wrong, that is
not a question. Fix it, validate it, and keep going.

## Write the question as a scenario

Second person, no jargon. Build the question out of the work actually in front
of you. Name the thing you were doing when you stopped, so the user is deciding
about their own task rather than about an abstraction. A scenario that could be
pasted into any other session is not a scenario.

Each option then describes the behavior that follows from picking it: what will
happen, what they will end up with, what it costs them. If an option cannot be
described without a type name or a flag, describe what that produces instead.
The user should be able to answer without reading the code.

## Options

Two to four, each a different outcome the user can picture. If two options
produce the same experience, they are one option.

The label is one to five words. The description carries the tradeoff: what they
get and what they give up. An option with no cost stated is not a real choice.

Lead with your recommendation and mark it `(Recommended)`. Say in its description
why it is the one you would pick, so the mark is a judgment they can disagree
with rather than an instruction. Being confident is not a reason to skip the
question, it is a reason to make the first option a single click.

Never dress a yes/no as a question with options. "Do it" and "Don't do it" is a
sentence.

Use `preview` when the options are artifacts to compare side by side, like two
layouts or two shapes of code. Single-select only.

## Do not spend a notification on

- Anything you could find out by reading a file, running a command, or searching
  the repo. A question is for what only the user knows.
- "Should I proceed?", "Does this look right?", "Is the plan good?"
- Something the user already settled earlier in this conversation.
- Uncertainty that belongs in a review. Finished work goes to `local-pr`. This is
  for work that is stuck.

## After they answer

Check the answer before you act on it. The user answered as a colleague, without
having the full context, so confirm the choice is actually possible here and does
not contradict something previously established.

If it holds, act on it and keep going. Do not restate it back, do not ask a
follow-up to confirm it, and do not re-open it out of your own uncertainty.

If it does not hold, that is a new blocker and it goes straight back with what
you found. Name the part that does not work and what it collides with. An answer
you cannot carry out is worth more to them than quiet compliance and a broken
result.

If the answer was a custom write-in that changes the shape of the work, say in
one line what you now understand the task to be, then do it.
