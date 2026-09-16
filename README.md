<h1 align="center">my-claude-stuff</h1>
<p align="center">Evan's personal Claude Code plugin.</p>

## Install

In Claude Code:

```
/plugin marketplace add evancdev/my-claude-stuff
/plugin install my-claude-stuff@personal
```

## CLI

The plugin's helper scripts are exposed through a single `my-claude` command.
Install it once per machine — this symlinks `my-claude` into `~/.local/bin` and
ensures that dir is on your `PATH`:

```
scripts/install-cli.py
```

Open a new shell (or `source` your rc file), then:

```
my-claude help                                  # list commands
my-claude secret set <KEY>                       # store a secret (hidden prompt)
my-claude secret list                            # list stored keys (values masked)
my-claude statusline install                     # register the statusline
```

To remove the command: `scripts/install-cli.py --uninstall`.
