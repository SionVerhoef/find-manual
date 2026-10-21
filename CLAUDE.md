# Working on this repository

These rules apply to anyone — human or agent — **editing this repository**. They are not
instructions for using the skill.

## This repository is public

`github.com/SionVerhoef/find-manual` is world-readable and installed into other people's
setups. Assume every commit is permanent and already read by a stranger.

## Manuals are research material, not repo content

Almost every real manual is copyrighted by its vendor. Use them to develop and test the
skill, but **never commit a manual, a page of one, or a close paraphrase of its text** —
not in `references/`, not in eval fixtures, not in commit messages.

- Keep test manuals in `manuals/` (gitignored) or anywhere outside the repo.
- `*.pdf` is gitignored. A fixture PDF you generated yourself may be added with
  `git add -f`, and only then.
- Evals describe *which* manual they need (title, version, where to get it) and what the
  right answer is (section number, page). They do not ship the manual.
- If a manual's licence explicitly permits redistribution, copy that licence next to it
  and say so in the commit.

## Keep it general

This is a general-purpose skill. Guidance must work for any manual a user brings, not just
the ones it was tested on. Vendor-specific tricks are fine when labelled as such.

## If something private or copyrighted has already been committed

**Stop and tell the maintainer.** Do not quietly delete it in a follow-up commit: the old
blob stays reachable in history on every clone and fork. Removing it needs a history
rewrite, which is the maintainer's decision.
