# Project hooks

## block_git_write_commands.py

A `PreToolUse` hook on the `Bash` and `PowerShell` tools that denies
`git commit` and `git push`.

**Why.** The master specification forbids committing or pushing without an
explicit instruction (sections 116, 117, 119). Enforcing that by having the
assistant remember it across sessions and context compactions is not a control.
This makes it mechanical.

**Blocked:** `git commit`, `git push` — including chained (`cd x && git push`),
flag-prefixed (`git -C path commit`, `git --no-pager push`), absolute-path
(`/usr/bin/git push`) and env-prefixed (`GIT_DIR=... git push`) forms.

**Allowed:** every read-only git command — `status`, `diff`, `log`, `branch`,
`show`, `remote`, and also `add` and `init`. The subcommand is identified by
parsing tokens, so `git log --grep="commit"` is not blocked.

**Fails open.** A malformed payload or an internal error allows the command
through rather than jamming the session. This is a guard rail against
forgetfulness, not a security boundary — anyone with shell access can run git
directly.

**Removing it.** Delete the `PreToolUse` entry from `.claude/settings.json`, or
manage it through `/hooks`.

**Activation.** Claude Code loads project settings that existed when the
session started. If `.claude/` was created mid-session, open `/hooks` once or
restart the session for the hook to take effect.

**Verifying it.** Pipe a payload straight at the script:

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"git push"}}' \
  | python .claude/hooks/block_git_write_commands.py     # -> permissionDecision deny

echo '{"tool_name":"Bash","tool_input":{"command":"git status"}}' \
  | python .claude/hooks/block_git_write_commands.py     # -> no output, allowed
```
