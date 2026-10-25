# claudectl

Use Claude Code with a separate profile for each customer, on Windows, macOS and Linux. Each profile has its own:

- sign-in or API key
- AI platform
- MCP servers
- skills and plugins
- session history and memory

Customers can run side by side in separate terminals. Switching never touches a session that's already running, and one customer's Jira or Confluence server never shows up in another customer's session.

claudectl doesn't change anything machine-wide. Your normal `claude` and `~/.claude` keep working exactly as before, and appear as the `default` profile.

It's pure Python 3.10+ with no dependencies beyond the standard library.

## Install

From PyPI (the package is `claude-profile`, the command is `claudectl`):

```bash
pipx install claude-profile      # or: uv tool install claude-profile   or: pip install claude-profile
claudectl install               # optional: adds the "Claude Profiles" Start menu picker on Windows
```

**Without Python:** download a standalone binary from [Releases](https://github.com/CloudOpsWorld/claudectl/releases) and run it once with `install`, which puts it in `~/.local/bin`:
- `claudectl-windows-amd64.exe`
- `claudectl-macos-arm64`
- `claudectl-linux-amd64`

Each release has `SHA256SUMS` and build provenance you can check with `gh attestation verify <file> --repo CloudOpsWorld/claudectl`.

**From source:**

```bash
python claudectl/cli.py install
```

This copies the single file to `~/.claudectl/app` and puts a launcher in `~/.local/bin`.

To remove claudectl, run `claudectl uninstall` (or `pipx uninstall claude-profile`). Your profiles are kept.

## Daily use

```bash
claudectl add acme                 # guided: name, AI platform, key or sign-in, bind this folder
cd ~/work/acme-repo
claudectl                          # starts Claude Code as ACME, because the folder is bound
claudectl tab globex               # new terminal tab locked to Globex
claudectl acme --resume            # any claude arguments pass through
claudectl mcp add jira -- npx -y <jira-mcp>   # goes into this folder's profile only
claudectl list                     # profiles, bound folders, active profile, Desktop profile
```

- **Bound folders** pick the profile automatically. If you start a different profile inside a bound folder, claudectl asks before continuing.
- **Everywhere else**, `claudectl` uses the active profile. Set it with `claudectl use <name>`.
- **Every session shows its customer** in Claude Code's status line and in the tab title.
- **On Windows**, tabs open in Windows Terminal with the customer's color. macOS uses Terminal.app. Linux uses the first terminal it finds: GNOME Terminal, Konsole, kitty, WezTerm, Alacritty or Xfce Terminal.

## Import your existing setup

```bash
claudectl import --scan     # just show what's in your current Claude setup
claudectl import            # guided: sort project folders into customer profiles
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
claudectl import --plan plan.json                  # write the suggestions to a file you can edit
claudectl import --apply plan.json --dry-run       # show what would happen
claudectl import --apply plan.json                 # copy
claudectl import --apply plan.json --move          # copy, then take it out of the default setup
```

Running `--apply` again is safe: it never overwrites anything and doesn't duplicate history.

**`--move`** removes the imported customer folders, history and MCP servers from your default setup:
- It refuses while any Claude Code session is running, including Desktop's Code tab.
- It backs up your config first.
- It puts everything it removes in `~/.claudectl/import-backups/`.
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

To add a platform, create `~/.claudectl/providers.json`: a JSON list of entries in the same shape as `BUILTIN_PROVIDERS` at the top of [claudectl/cli.py](claudectl/cli.py). An entry with an existing `id` replaces the built-in one. To change a profile's models, run `claudectl edit <name>`.

**Where API keys are stored:**
- **Windows:** encrypted with DPAPI, so only your Windows user can read them.
- **macOS:** in your login Keychain.
- **Linux:** in your keyring through `secret-tool`. Without a keyring, in a file only you can read.

## Claude Desktop

Desktop holds one sign-in at a time, so it switches with a restart:

1. Quit Desktop.
2. Run `claudectl desktop acme`.
3. Start Desktop.

This swaps the local MCP servers in Desktop's config. Each customer's servers are saved automatically when you switch away.

Cloud connectors added on claude.ai belong to the signed-in account and follow it. If the customer uses a different Claude account, sign out and back in.

**Not isolated:** Desktop's own Code tab uses Desktop's built-in Claude Code and login. Do isolated customer work in a terminal with claudectl.

## Where things live

Everything is in `~/.claudectl`, on every OS. On Windows this is deliberately not AppData: Microsoft Store Python redirects new files there into a private folder that Claude Code can't see.

Each profile lives in `~/.claudectl/profiles/<name>/`:
- `profile.json`: platform, models, how the key is stored
- `config/`: Claude Code's own folder for this customer (login, MCP, skills, plugins, history, memory)
- `desktop-mcp.json`: this customer's Claude Desktop MCP servers

Set `CLAUDECTL_HOME` to put the data folder somewhere else.
