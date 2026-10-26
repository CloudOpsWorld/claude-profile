# claude-profile

Use Claude Code with a separate profile for each customer, on Windows, macOS and Linux. Each profile has its own:

- sign-in or API key
- AI platform
- MCP servers
- skills and plugins
- session history and memory

Customers can run side by side in separate terminals. Switching never touches a session that's already running, and one customer's Jira or Confluence server never shows up in another customer's session.

claude-profile doesn't change anything machine-wide. Your normal `claude` and `~/.claude` keep working exactly as before, and appear as the `default` profile.

It's pure Python 3.10+ with no dependencies beyond the standard library.

## Install

From PyPI:

```bash
pipx install claude-profile      # or: uv tool install claude-profile   or: pip install claude-profile
claude-profile install           # optional: adds the "Claude Profiles" Start menu picker on Windows
```

**Without Python:** download a standalone binary from [Releases](https://github.com/CloudOpsWorld/claude-profile/releases) and run it once with `install`, which puts it in `~/.local/bin`:
- `claude-profile-windows-amd64.exe`
- `claude-profile-macos-arm64`
- `claude-profile-linux-amd64`

Each release has `SHA256SUMS` and build provenance you can check with `gh attestation verify <file> --repo CloudOpsWorld/claude-profile`.

**From source:**

```bash
python claude_profile/cli.py install
```

This copies the single file to `~/.claude-profile/app` and puts a launcher in `~/.local/bin`.

To remove claude-profile, run `claude-profile uninstall` (or `pipx uninstall claude-profile`). Your profiles are kept.

**Upgrading from 1.x:** versions 1.x used the command `claudectl` and kept data in `~/.claudectl`. From 2.0 everything is named `claude-profile`. If you made profiles with 1.x, close their sessions and rename `~/.claudectl` to `~/.claude-profile`. Then run `claude-profile install` once so every profile's status line points at the new command.

## Daily use

```bash
claude-profile add acme                 # guided: name, AI platform, key or sign-in, bind this folder
cd ~/work/acme-repo
claude-profile                          # starts Claude Code as ACME, because the folder is bound
claude-profile tab globex               # new terminal tab locked to Globex
claude-profile acme --resume            # any claude arguments pass through
claude-profile mcp add jira -- npx -y <jira-mcp>   # goes into this folder's profile only
claude-profile list                     # profiles, bound folders, active profile, Desktop profile
```

- **Bound folders** pick the profile automatically. If you start a different profile inside a bound folder, claude-profile asks before continuing.
- **Everywhere else**, `claude-profile` uses the active profile. Set it with `claude-profile use <name>`.
- **Every session shows its customer** in Claude Code's status line and in the tab title.
- **On Windows**, tabs open in Windows Terminal with the customer's color. macOS uses Terminal.app. Linux uses the first terminal it finds: GNOME Terminal, Konsole, kitty, WezTerm, Alacritty or Xfce Terminal.

## Import your existing setup

```bash
claude-profile import --scan     # just show what's in your current Claude setup
claude-profile import            # guided: sort project folders into customer profiles
```

**What the scan finds:**
- every project folder Claude has history for (worktrees are folded into their repo)
- MCP servers, both user-wide and per-project
- skills, agents, commands and output styles
- your `CLAUDE.md`, plugins and preferences
- Claude Desktop's MCP servers
- leftovers from older switcher scripts: extra `~/.claude-*` config folders, system-wide `ANTHROPIC_*` variables, and lines in your PowerShell or shell profile

It skips temporary folders, folders that no longer exist, your home folder and drive roots.

**The guided import** suggests customers from your folder layout. `D:\work\acme\api` and `D:\work\acme\web` become one `acme` profile, tied to `D:\work\acme`. You can:
- press Enter to accept each suggestion
- type a name to rename it or merge it into another profile
- press `k` to keep those folders in your default setup

Then you pick:
- the AI platform for each new profile
- which profiles get which MCP servers
- whether new profiles get your skills, `CLAUDE.md`, preferences and plugins (plugins are reinstalled cleanly in each profile)

**It only copies.** Each project's history, memory, prompt history, trust settings and per-project MCP servers go into its profile. Your current setup isn't changed, so it's safe while Claude is running.

**Your own sign-in, account details and caches are never copied into a customer profile.** The one exception: a `~/.claude-*` folder left by an older switcher is adopted whole, including its sign-in, because that sign-in belongs to the customer.

You can also work from a file, which is handy for many folders or for repeating the import:

```bash
claude-profile import --plan plan.json                  # write the suggestions to a file you can edit
claude-profile import --apply plan.json --dry-run       # show what would happen
claude-profile import --apply plan.json                 # copy
claude-profile import --apply plan.json --move          # copy, then take it out of the default setup
```

Running `--apply` again is safe: it never overwrites anything and doesn't duplicate history.

**`--move`** removes the imported customer folders, history and MCP servers from your default setup:
- It refuses while any Claude Code session is running, including Desktop's Code tab.
- It backs up your config first.
- It puts everything it removes in `~/.claude-profile/import-backups/`.
- Desktop's Code tab uses your default setup, so moved history no longer shows up there.

`--from DIR` imports from another Claude config folder instead of `~/.claude`.

## AI platforms

| Platform | How it connects |
|---|---|
| Claude subscription: Pro, Max, Team, Enterprise | Browser sign-in on first start. You can lock it to the customer's org UUID. |
| Anthropic API key | Pay per use. |
| DeepSeek | Native Claude-compatible endpoint. |
| OpenRouter | GPT, Gemini and others. Use this for OpenAI customers, with BYOK for their own OpenAI key. |
| Any Claude-compatible URL | For example a LiteLLM proxy in front of a direct OpenAI key, Moonshot, Z.ai or a company gateway. |
| AWS Bedrock, Google Vertex | Claude through the customer's own cloud account. |

To add a platform, create `~/.claude-profile/providers.json`: a JSON list of entries in the same shape as `BUILTIN_PROVIDERS` at the top of [claude_profile/cli.py](claude_profile/cli.py). An entry with an existing `id` replaces the built-in one. To change a profile's models, run `claude-profile edit <name>`.

**Where API keys are stored:**
- **Windows:** encrypted with DPAPI, so only your Windows user can read them.
- **macOS:** in your login Keychain.
- **Linux:** in your keyring through `secret-tool`. Without a keyring, in a file only you can read.

## Claude Desktop

Desktop holds one sign-in at a time, so it switches with a restart:

1. Quit Desktop.
2. Run `claude-profile desktop acme`.
3. Start Desktop.

This swaps the local MCP servers in Desktop's config. Each customer's servers are saved automatically when you switch away.

Cloud connectors added on claude.ai belong to the signed-in account and follow it. If the customer uses a different Claude account, sign out and back in.

**Not isolated:** Desktop's own Code tab uses Desktop's built-in Claude Code and login. Do isolated customer work in a terminal with claude-profile.

## Where things live

Everything is in `~/.claude-profile`, on every OS. On Windows this is deliberately not AppData: Microsoft Store Python redirects new files there into a private folder that Claude Code can't see.

Each profile lives in `~/.claude-profile/profiles/<name>/`:
- `profile.json`: platform, models, how the key is stored
- `config/`: Claude Code's own folder for this customer (login, MCP, skills, plugins, history, memory)
- `desktop-mcp.json`: this customer's Claude Desktop MCP servers

Set `CLAUDE_PROFILE_HOME` to put the data folder somewhere else.
