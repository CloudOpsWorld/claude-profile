#!/usr/bin/env python3
"""claude-profile - per-customer profiles for Claude Code and Claude Desktop.

Each profile is its own Claude Code config folder (login, MCP servers, skills,
plugins, history, memory), selected per process through CLAUDE_CONFIG_DIR, so
any number of customers run side by side without touching each other.
Windows, macOS and Linux. Python 3.9+, standard library only.
"""

import getpass
import glob
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from . import __version__ as VERSION
except ImportError:  # run as a standalone script (claude-profile install copies this file)
    VERSION = "2.0.0"

APP = "claude-profile"
DIST = APP  # PyPI package name (same as the command)
MODULE = "claude_profile"  # Python module name
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
FROZEN = bool(getattr(sys, "frozen", False))  # PyInstaller binary
SELF = sys.executable if FROZEN else os.path.abspath(__file__)

# 'claude-profile --home <dir> ...' uses another data folder. New terminal tabs get
# their environment from the terminal app, not from us, so they are passed this.
if len(sys.argv) > 2 and sys.argv[1] == "--home":
    os.environ["CLAUDE_PROFILE_HOME"] = sys.argv[2]
    del sys.argv[1:3]

# ~/.claude-profile on every OS, next to ~/.claude. (Not AppData: Microsoft Store
# Python redirects new files there into a private folder Claude can't see.)
HOME = Path(os.environ.get("CLAUDE_PROFILE_HOME") or Path.home() / ".claude-profile")
PROFILES = HOME / "profiles"
BINDINGS_FILE = HOME / "bindings.json"
ACTIVE_FILE = HOME / "active.txt"
DESKTOP_STATE = HOME / "desktop.txt"

PALETTE = ["#E8590C", "#1C7ED6", "#2B8A3E", "#AE3EC9", "#F08C00", "#0CA678", "#C2255C", "#5C7CFA"]
RESERVED = set("add list ls use bind unbind status tab shell desktop key edit remove rm menu doctor help run "
               "install uninstall version statusline import mcp plugin config agents".split())
# Commands that belong to claude itself; they run in this folder's profile.
CLAUDE_SUBCOMMANDS = {"mcp", "plugin", "config", "agents"}

# Variables that must never leak from the parent shell (or from a Claude
# Desktop session the shell was started from) into a profile's session.
SCRUB = re.compile(r"^(ANTHROPIC_|CLAUDE_CODE_|CLAUDECODE$|CLAUDE_CONFIG_DIR$|CLAUDE_AGENT_SDK|CLAUDE_PID$|"
                   r"OPENAI_|CLAUDE_PROFILE$|CLOUD_ML_REGION$)", re.I)
SCRUB_KEEP = {"CLAUDE_CODE_GIT_BASH_PATH"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")

# Built-in AI platforms. Add or override entries (same "id") in ~/.claude-profile/providers.json.
BUILTIN_PROVIDERS = [
    {"id": "claude", "label": "Claude subscription (Pro / Max / Team / Enterprise) - browser sign-in", "secret": False,
     "prompts": [{"key": "org", "label": "Organization UUID to lock sign-in to (optional, Team/Enterprise)", "default": ""}],
     "env": {}, "settings": {"forceLoginMethod": "claudeai", "forceLoginOrgUUID": "{org}"}, "summary": "Claude subscription"},
    {"id": "anthropic-api", "label": "Anthropic API key (Console, pay per use)", "secret": True, "prompts": [],
     "env": {"ANTHROPIC_API_KEY": "{secret}"}, "settings": {}, "summary": "Anthropic API"},
    {"id": "deepseek", "label": "DeepSeek (native Anthropic-compatible endpoint)", "secret": True,
     "prompts": [{"key": "model", "label": "Main model", "default": "deepseek-flash[1m]"},
                 {"key": "small", "label": "Fast model (background tasks, subagents)", "default": "deepseek-flash"}],
     "env": {"ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic", "ANTHROPIC_AUTH_TOKEN": "{secret}",
             "ANTHROPIC_MODEL": "{model}", "ANTHROPIC_DEFAULT_OPUS_MODEL": "{model}",
             "ANTHROPIC_DEFAULT_SONNET_MODEL": "{model}", "ANTHROPIC_DEFAULT_HAIKU_MODEL": "{small}",
             "CLAUDE_CODE_SUBAGENT_MODEL": "{small}", "CLAUDE_CODE_EFFORT_LEVEL": "max",
             "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "786432"},
     "settings": {}, "summary": "DeepSeek {model}"},
    {"id": "openrouter", "label": "OpenRouter (OpenAI GPT, Gemini, ... - OpenAI customers can use their own key via OpenRouter BYOK)",
     "secret": True,
     "prompts": [{"key": "model", "label": "Main model (OpenRouter id)", "default": "openai/gpt-5"},
                 {"key": "small", "label": "Fast model (background tasks, subagents)", "default": "{model}"}],
     "env": {"ANTHROPIC_BASE_URL": "https://openrouter.ai/api", "ANTHROPIC_AUTH_TOKEN": "{secret}",
             "ANTHROPIC_MODEL": "{model}", "ANTHROPIC_DEFAULT_OPUS_MODEL": "{model}",
             "ANTHROPIC_DEFAULT_SONNET_MODEL": "{model}", "ANTHROPIC_DEFAULT_HAIKU_MODEL": "{small}",
             "CLAUDE_CODE_SUBAGENT_MODEL": "{small}"},
     "settings": {}, "summary": "OpenRouter {model}"},
    {"id": "compatible", "label": "Any Anthropic-compatible endpoint (LiteLLM proxy for a direct OpenAI key, Moonshot, Z.ai, company gateway)",
     "secret": True,
     "prompts": [{"key": "baseUrl", "label": "Base URL (e.g. http://localhost:4000 for LiteLLM)", "default": ""},
                 {"key": "model", "label": "Main model", "default": ""},
                 {"key": "small", "label": "Fast model (background tasks, subagents)", "default": "{model}"}],
     "env": {"ANTHROPIC_BASE_URL": "{baseUrl}", "ANTHROPIC_AUTH_TOKEN": "{secret}", "ANTHROPIC_MODEL": "{model}",
             "ANTHROPIC_DEFAULT_OPUS_MODEL": "{model}", "ANTHROPIC_DEFAULT_SONNET_MODEL": "{model}",
             "ANTHROPIC_DEFAULT_HAIKU_MODEL": "{small}", "CLAUDE_CODE_SUBAGENT_MODEL": "{small}"},
     "settings": {}, "summary": "{model} @ {baseUrl}"},
    {"id": "bedrock", "label": "Claude through the customer's AWS Bedrock", "secret": False,
     "prompts": [{"key": "awsProfile", "label": "AWS CLI profile name", "default": ""},
                 {"key": "region", "label": "AWS region", "default": "us-east-1"}],
     "env": {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_PROFILE": "{awsProfile}", "AWS_REGION": "{region}"},
     "settings": {}, "summary": "Bedrock ({awsProfile})"},
    {"id": "vertex", "label": "Claude through the customer's Google Vertex AI", "secret": False,
     "prompts": [{"key": "project", "label": "GCP project id", "default": ""},
                 {"key": "region", "label": "Region", "default": "global"}],
     "env": {"CLAUDE_CODE_USE_VERTEX": "1", "ANTHROPIC_VERTEX_PROJECT_ID": "{project}", "CLOUD_ML_REGION": "{region}"},
     "settings": {}, "summary": "Vertex ({project})"},
]


# ------------------------------------------------------------------ output

def _enable_vt():
    """Lets colors and the tab-title escape work in the classic Windows console."""
    if not IS_WIN:
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if k32.GetConsoleMode(h, ctypes.byref(mode)):
            k32.SetConsoleMode(h, mode.value | 0x0004)
    except Exception:
        pass


TTY = sys.stdout.isatty()


def _paint(code):
    return lambda s: f"\x1b[{code}m{s}\x1b[0m" if TTY else str(s)


cyan, gray, green, yellow, red = (_paint(c) for c in ("36", "90", "32", "33", "31"))


def say(s=""):
    print(s)


def info(s):
    print(gray(s))


class CliError(Exception):
    pass


def fail(msg):
    raise CliError(msg)


def set_title(t):
    if TTY:
        sys.stdout.write(f"\x1b]0;{t}\x07")
        sys.stdout.flush()


# ------------------------------------------------------------------ files

def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ------------------------------------------------------------------ prompts

def ask(q):
    try:
        return input(q).strip().lstrip("﻿")  # Windows pipes can prepend a BOM
    except (EOFError, OSError):  # no usable stdin (scripts, CI)
        return ""


def confirm(q):
    if not sys.stdin.isatty():
        return False
    return ask(f"{q} [y/N] ").lower() in ("y", "yes")


def ask_hidden(q):
    if not sys.stdin.isatty():
        return ask(q)
    return getpass.getpass(q).strip()


# ------------------------------------------------------------------ processes

def run_capture(cmd, input_text=None):
    r = subprocess.run(cmd, input=input_text, capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"{Path(cmd[0]).name} failed: {(r.stderr or r.stdout).strip()}")
    return r.stdout


def powershell(command, input_text=None):
    return run_capture(["powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command], input_text)


def run_interactive(exe, args, env):
    """Runs an interactive child in this terminal and returns its exit code."""
    if not IS_WIN:
        # Replace this process, so signals, job control and exit codes behave
        # exactly as if the user had started the child directly.
        os.execve(exe, [exe, *args], env)
    # Ctrl+C belongs to the child (Claude uses it to interrupt).
    old = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        return subprocess.call([exe, *args], env=env)
    finally:
        signal.signal(signal.SIGINT, old)


def start_detached(cmd, cwd=None):
    kw = {"cwd": cwd, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if IS_WIN:
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(cmd, **kw)


def self_cmd():
    """How to call this tool again (status line, new tabs)."""
    if FROZEN:
        return [sys.executable]
    if __package__ and not SELF_COPY:
        return [sys.executable, "-m", MODULE]
    return [sys.executable, SELF]


# True once 'claude-profile install' has copied this file to ~/.claude-profile/app.
SELF_COPY = False


# ------------------------------------------------------------------ secrets
# Windows: DPAPI (only this Windows user can decrypt; ciphertext in profile.json).
# macOS: login Keychain. Linux: Secret Service via secret-tool, else a 0600 file.

if IS_WIN:
    import base64
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _dpapi(fn, data):
        buf = ctypes.create_string_buffer(data, len(data))
        blob_in = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = _Blob()
        if not fn(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out)):
            raise ctypes.WinError()
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def secret_file(profile):
    return profile_dir(profile) / "secret"


def protect_secret(profile, plain):
    if IS_WIN:
        enc = _dpapi(ctypes.windll.crypt32.CryptProtectData, plain.encode("utf-8"))
        return {"store": "dpapi", "data": base64.b64encode(enc).decode()}
    if IS_MAC:
        # 'security -i' reads the command from stdin, so the key never shows up in the process list.
        q = lambda s: '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
        r = subprocess.run(["security", "-i"], input=f"add-generic-password -U -s {q(APP)} -a {q(profile)} -w {q(plain)}\n",
                           capture_output=True, text=True)
        if r.returncode != 0 or "error" in (r.stdout + r.stderr).lower():
            fail(f"security failed: {(r.stderr or r.stdout).strip()}")
        return {"store": "keychain"}
    if shutil.which("secret-tool"):
        r = subprocess.run(["secret-tool", "store", "--label", f"{APP} {profile}", "service", APP, "profile", profile],
                           input=plain, text=True, capture_output=True)
        if r.returncode == 0:
            return {"store": "libsecret"}
    f = secret_file(profile)
    f.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as h:
        h.write(plain)
    info(f"{APP}: no keyring available; key saved to {f}, readable only by you.")
    return {"store": "file"}


def unprotect_secret(profile, s):
    if not s:
        fail("none stored")
    store = s.get("store")
    if store == "dpapi" and IS_WIN:
        return _dpapi(ctypes.windll.crypt32.CryptUnprotectData, base64.b64decode(s["data"])).decode("utf-8")
    if store == "keychain" and IS_MAC:
        return run_capture(["security", "find-generic-password", "-s", APP, "-a", profile, "-w"]).rstrip("\r\n")
    if store == "libsecret" and not IS_WIN:
        return run_capture(["secret-tool", "lookup", "service", APP, "profile", profile]).rstrip("\r\n")
    if store == "file":
        return secret_file(profile).read_text().strip()
    fail(f"the key was stored with '{store}' on another OS; run '{APP} key {profile}' here")


def forget_secret(profile, s):
    if not s:
        return
    try:
        if s.get("store") == "keychain":
            subprocess.run(["security", "delete-generic-password", "-s", APP, "-a", profile], capture_output=True)
        elif s.get("store") == "libsecret":
            subprocess.run(["secret-tool", "clear", "service", APP, "profile", profile], capture_output=True)
    except OSError:
        pass


# ------------------------------------------------------------------ providers

def get_providers():
    providers = {p["id"]: p for p in BUILTIN_PROVIDERS}
    for p in read_json(HOME / "providers.json", []) or []:
        providers[p["id"]] = p
    return list(providers.values())


def expand(text, values):
    if not isinstance(text, str):
        return text
    return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), text)


# ------------------------------------------------------------------ profiles

def valid_name(n):
    return bool(n) and bool(NAME_RE.match(n)) and n not in RESERVED


def profile_dir(n):
    return PROFILES / n


def config_dir(n):
    return None if n == "default" else profile_dir(n) / "config"


def profile_exists(n):
    if n == "default":
        return True
    return valid_name(n) and (profile_dir(n) / "profile.json").is_file()


def get_profile(n):
    if n == "default":
        return {"name": "default", "label": "Default (your normal ~/.claude)", "provider": "claude",
                "summary": "your usual setup", "color": "#868E96", "env": {}, "secret": None}
    if not profile_exists(n):
        fail(f"no profile named '{n}'. Run '{APP} list' or '{APP} add {n}'.")
    p = read_json(profile_dir(n) / "profile.json")
    if not p:
        fail(f"profile '{n}' is unreadable")
    return p


def save_profile(p):
    write_json(profile_dir(p["name"]) / "profile.json", p)


def profile_names():
    names = sorted(d.name for d in PROFILES.iterdir() if d.is_dir() and profile_exists(d.name)) if PROFILES.is_dir() else []
    return ["default", *names]


# ------------------------------------------------------------------ bindings

def norm_path(p):
    return os.path.normpath(os.path.abspath(p))


def path_key(p):
    n = norm_path(p)
    return n.lower() if (IS_WIN or IS_MAC) else n


def get_bindings():
    return read_json(BINDINGS_FILE, {}) or {}


def find_binding(directory):
    here = path_key(directory)
    best = None
    for folder, prof in get_bindings().items():
        f = path_key(folder)
        prefix = f if f.endswith(os.sep) else f + os.sep
        if (here == f or here.startswith(prefix)) and (best is None or len(f) > best[2]):
            best = (norm_path(folder), prof, len(f))
    return best and (best[0], best[1])


def active_name():
    n = read_text(ACTIVE_FILE)
    return n if n and profile_exists(n) else "default"


def resolve_here(directory):
    b = find_binding(directory)
    if b and profile_exists(b[1]):
        return b[1], f"folder {b[0]} is bound to it"
    return active_name(), "active profile"


# ------------------------------------------------------------------ environment

def profile_env(p):
    env = {k: v for k, v in os.environ.items() if not (SCRUB.match(k) and k.upper() not in SCRUB_KEEP)}
    cd = config_dir(p["name"])
    if cd:
        env["CLAUDE_CONFIG_DIR"] = str(cd)
    env["CLAUDE_PROFILE"] = p["name"]
    env["CLAUDE_CODE_DISABLE_TERMINAL_TITLE"] = "1"
    plain = None
    for k, v in (p.get("env") or {}).items():
        v = str(v)
        if "{secret}" in v:
            if plain is None:
                try:
                    plain = unprotect_secret(p["name"], p.get("secret"))
                except CliError as e:
                    fail(f"profile '{p['name']}' has no usable API key ({e}). Run '{APP} key {p['name']}'.")
            v = v.replace("{secret}", plain)
        if v:
            env[k] = v
    return env


def banner(p):
    say("")
    say(f"  {cyan('>> ' + p['label'] + '  [' + p['name'] + ']')}  {gray(p.get('summary') or p.get('provider'))}")
    info(f"     config: {config_dir(p['name']) or '~/.claude (default)'}")
    say("")


# ------------------------------------------------------------------ run, shell, tab

def cmd_run(name, claude_args):
    here = os.getcwd()
    if not name:
        name, reason = resolve_here(here)
        info(f"{APP}: using '{name}' ({reason})")
    else:
        b = find_binding(here)
        if b and b[1] != name:
            say(yellow(f"{APP}: this folder belongs to profile '{b[1]}', not '{name}'."))
            if not confirm(f"Start '{name}' here anyway?"):
                return 1
    p = get_profile(name)
    exe = shutil.which("claude")
    if not exe:
        fail("'claude' is not on PATH. Install Claude Code first.")
    env = profile_env(p)
    banner(p)
    set_title(f"Claude - {p['label']}")
    sys.stdout.flush()
    return run_interactive(exe, claude_args, env)


def cmd_shell(name, start):
    """An interactive shell locked to a profile; with --start it opens claude first."""
    name = name or resolve_here(os.getcwd())[0]
    p = get_profile(name)
    env = profile_env(p)
    banner(p)
    set_title(f"Claude - {p['label']}")
    info(f"This shell is locked to '{name}'. Type 'claude' as often as you like; exit or close the tab to leave.")
    sys.stdout.flush()
    if IS_WIN:
        ps = shutil.which("pwsh") or shutil.which("powershell")
        script = f"function global:prompt {{ Write-Host '[{name}] ' -NoNewline -ForegroundColor Cyan; 'PS ' + (Get-Location) + '> ' }}"
        if start:
            script += "; claude"
        return run_interactive(ps, ["-NoExit", "-NoLogo", "-Command", script], env)
    sh = os.environ.get("SHELL") or "/bin/sh"
    env["PS1"] = f"[{name}] " + os.environ.get("PS1", r"\w \$ ")
    args = ["-i", "-c", f"claude; exec {shlex.quote(sh)} -i"] if start else ["-i"]
    return run_interactive(sh, args, env)


def tab_folder(name):
    here = os.getcwd()
    b = find_binding(here)
    if b and b[1] == name:
        return here
    bound = sorted(f for f, p in get_bindings().items() if p == name)
    return bound[0] if bound else here


def cmd_tab(name, directory):
    name = name or resolve_here(os.getcwd())[0]
    p = get_profile(name)
    directory = norm_path(directory) if directory else tab_folder(name)
    title = p["label"].replace(";", "").replace('"', "")
    home_arg = ["--home", str(HOME)] if os.environ.get("CLAUDE_PROFILE_HOME") else []
    inner = [*self_cmd(), *home_arg, "shell", name, "--start"]
    if IS_WIN:
        wt = shutil.which("wt")
        if wt:
            start_detached([wt, "-w", "0", "new-tab", "--title", title, "--tabColor", p.get("color") or "#868E96",
                            "--suppressApplicationTitle", "-d", directory, *inner], directory)
        else:
            subprocess.Popen(inner, cwd=directory, creationflags=0x00000010)  # CREATE_NEW_CONSOLE
    elif IS_MAC:
        line = f"cd {shlex.quote(directory)} && {shlex.join(inner)}"
        script = line.replace("\\", "\\\\").replace('"', '\\"')
        start_detached(["osascript", "-e", f'tell application "Terminal" to do script "{script}"',
                        "-e", 'tell application "Terminal" to activate'])
    else:
        terms = [
            ["gnome-terminal", "--tab", "--title", title, f"--working-directory={directory}", "--", *inner],
            ["konsole", "--new-tab", "--workdir", directory, "-e", *inner],
            ["kitty", "--directory", directory, "--title", title, *inner],
            ["wezterm", "start", "--cwd", directory, "--", *inner],
            ["alacritty", "--working-directory", directory, "--title", title, "-e", *inner],
            ["xfce4-terminal", "--tab", "--title", title, "--working-directory", directory, "-x", *inner],
            ["x-terminal-emulator", "-e", *inner],
        ]
        term = next((t for t in terms if shutil.which(t[0])), None)
        if not term:
            say(f"No supported terminal found. Run this in a new terminal:\n  cd {shlex.quote(directory)} && {APP} shell {name} --start")
            return 0
        start_detached(term, directory)
    info(f"{APP}: opened a new tab for '{name}' in {directory}")
    return 0


# ------------------------------------------------------------------ status line

def status_line_setting(name):
    cmd = " ".join(f'"{Path(c).as_posix()}"' for c in self_cmd())
    if os.environ.get("CLAUDE_PROFILE_HOME"):
        cmd += f' --home "{HOME.as_posix()}"'
    return {"type": "command", "command": f"{cmd} statusline {name}"}


def cmd_statusline(name):
    """Printed at the bottom of every Claude Code session of a profile."""
    p = get_profile(name)
    model = ""
    if not sys.stdin.isatty():
        try:
            m = json.load(sys.stdin).get("model") or {}
            model = m.get("display_name") or m.get("id") or ""
        except ValueError:
            pass
    parts = [p.get("summary", "")] + ([model] if model else [])
    sys.stdout.write(f"\x1b[1;36m{p['label']}\x1b[0m  " + " \u00b7 ".join(parts))
    return 0


# ------------------------------------------------------------------ add, list, ...

def parse_options(tokens):
    opt = {"set": {}}
    pos = []
    flags = {"bind", "no-bind", "start", "scan", "move", "dry-run", "no-plugins"}
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.startswith("--") and len(t) > 2:
            k = t[2:]
            if k in flags:
                opt[k] = True
            else:
                i += 1
                v = tokens[i] if i < len(tokens) else ""
                if k == "set":
                    key, _, val = v.partition("=")
                    opt["set"][key] = val
                else:
                    opt[k] = v
        else:
            pos.append(t)
        i += 1
    return opt, pos


def create_profile(name, label, prov, values, plain_key=None, color=None):
    """Writes a new profile; plain_key may be None for a key set later with 'claude-profile key'."""
    values = dict(values)
    for q in prov.get("prompts") or []:
        values.setdefault(q["key"], expand(q.get("default", ""), values))
    env = {k: expand(v, values) for k, v in (prov.get("env") or {}).items()}
    settings = {k: expand(v, values) for k, v in (prov.get("settings") or {}).items()}
    settings = {k: v for k, v in settings.items() if v != ""}
    if "forceLoginMethod" not in settings:
        settings.pop("forceLoginOrgUUID", None)

    summary = expand(prov.get("summary") or prov["id"], values)
    color = color or PALETTE[(len(profile_names()) - 1) % len(PALETTE)]
    config_dir(name).mkdir(parents=True, exist_ok=True)
    p = {"name": name, "label": label, "provider": prov["id"], "summary": summary, "color": color, "env": env,
         "secret": None, "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if plain_key:
        try:
            p["secret"] = protect_secret(name, plain_key)
        except Exception:
            shutil.rmtree(profile_dir(name), ignore_errors=True)
            raise
    save_profile(p)

    # Always visible inside Claude Code: which customer this session belongs to.
    settings["statusLine"] = status_line_setting(name)
    write_json(config_dir(name) / "settings.json", settings)
    return p


def cmd_add(name, opt):
    name = (name or ask("Profile short name (lowercase, e.g. acme): ")).lower()
    if not valid_name(name) or name == "default":
        fail(f"invalid name '{name}'. Use lowercase letters, digits, - and _, and not a {APP} command name.")
    if profile_exists(name):
        fail(f"profile '{name}' already exists.")
    providers = get_providers()
    interactive = not opt.get("provider")

    label = opt.get("label") or (ask(f"Display name [{name}]: ") if interactive else "") or name

    if opt.get("provider"):
        prov = next((p for p in providers if p["id"] == opt["provider"]), None)
        if not prov:
            fail(f"unknown provider '{opt['provider']}'. Known: {', '.join(p['id'] for p in providers)}")
    else:
        say("\nWhich AI platform does this customer use?")
        for i, p in enumerate(providers, 1):
            say(f"  {i}) {p['label']}")
        choice = ask("Number: ")
        if not choice.isdigit() or not 1 <= int(choice) <= len(providers):
            fail("invalid choice.")
        prov = providers[int(choice) - 1]

    values = {}
    for q in prov.get("prompts") or []:
        default = expand(q.get("default", ""), values)
        if q["key"] in opt["set"]:
            v = opt["set"][q["key"]]
        else:
            v = ask(f"{q['label']}{f' [{default}]' if default else ''}: ") if interactive else ""
            v = v or default
        values[q["key"]] = v

    plain_key = None
    if prov.get("secret"):
        plain_key = opt.get("key") or ask_hidden("API key (hidden, stored in your OS keychain): ")
        if not plain_key:
            fail("no key entered.")

    p = create_profile(name, label, prov, values, plain_key, opt.get("color"))
    say(green(f"\nCreated profile '{name}' ({label}, {p['summary']})."))
    here = os.getcwd()
    if not opt.get("no-bind") and (opt.get("bind") or (interactive and confirm(f"Bind the current folder ({here}) to '{name}'?"))):
        cmd_bind(name, here)
    say("")
    say(f"Next:  {APP} {name}              start Claude Code with this profile here")
    say(f"       {APP} tab {name}          open it in a new terminal tab")
    if prov["id"] == "claude":
        say("       (first start asks you to sign in with the customer account)")
    say(f"       {APP} {name} mcp add ...  add this customer's MCP servers (only this profile sees them)")
    return 0


def cmd_list():
    active, here, desk = active_name(), resolve_here(os.getcwd())[0], read_text(DESKTOP_STATE)
    bindings = get_bindings()
    say("")
    for n in profile_names():
        p = get_profile(n)
        marks = [m for m, on in (("active", n == active), ("here", n == here), ("desktop", n == desk)) if on]
        row = f"{'*' if n == here else ' '} {cyan(n.ljust(14))} {p['label'].ljust(30)} {p.get('summary', '')}"
        say(row + (f"  {yellow('(' + ', '.join(marks) + ')')}" if marks else ""))
        for f in sorted(f for f, pn in bindings.items() if pn == n):
            info(f"                 bound: {f}")
    say("")
    return 0


def cmd_use(name):
    if not name:
        say(active_name())
        return 0
    if not profile_exists(name):
        fail(f"no profile named '{name}'.")
    write_text(ACTIVE_FILE, name)
    say(f"Active profile is now '{name}'. New sessions outside bound folders use it; running sessions are unchanged.")
    return 0


def cmd_bind(name, directory=None):
    if not name:
        fail(f"usage: {APP} bind <profile> [folder]")
    if not profile_exists(name):
        fail(f"no profile named '{name}'.")
    p = norm_path(directory or os.getcwd())
    m = {k: v for k, v in get_bindings().items() if path_key(k) != path_key(p)}
    m[p] = name
    write_json(BINDINGS_FILE, m)
    say(f"Bound {p} (and everything below it) to '{name}'.")
    return 0


def cmd_unbind(directory=None):
    b = find_binding(directory or os.getcwd())
    if not b:
        say("This folder is not bound.")
        return 0
    write_json(BINDINGS_FILE, {k: v for k, v in get_bindings().items() if path_key(k) != path_key(b[0])})
    say(f"Removed binding {b[0]} -> '{b[1]}'.")
    return 0


def cmd_key(name):
    if not name or name == "default":
        fail(f"usage: {APP} key <profile>")
    p = get_profile(name)
    k = ask_hidden(f"New API key for '{name}' (hidden): ")
    if not k:
        fail("no key entered.")
    p["secret"] = protect_secret(name, k)
    save_profile(p)
    say("Key updated.")
    return 0


def cmd_remove(name):
    if not name or name == "default":
        fail(f"usage: {APP} remove <profile>")
    p = get_profile(name)
    say(yellow(f"This deletes profile '{name}' including its sign-in, MCP servers, skills, history and memory."))
    if ask("Type the profile name to confirm: ") != name:
        say("Cancelled.")
        return 1
    forget_secret(name, p.get("secret"))
    shutil.rmtree(profile_dir(name))
    write_json(BINDINGS_FILE, {k: v for k, v in get_bindings().items() if v != name})
    if read_text(ACTIVE_FILE) == name:
        ACTIVE_FILE.unlink(missing_ok=True)
    say(f"Removed '{name}'.")
    return 0


def cmd_status():
    n, reason = resolve_here(os.getcwd())
    p = get_profile(n)
    say(f"Here: '{n}' - {p['label']}, {p.get('summary', '')}  ({reason})")
    if os.environ.get("CLAUDE_PROFILE"):
        say(f"This shell is locked to: '{os.environ['CLAUDE_PROFILE']}'")
    say(f"Active: '{active_name()}'")
    if read_text(DESKTOP_STATE):
        say(f"Claude Desktop: '{read_text(DESKTOP_STATE)}'")
    return 0


def cmd_edit(name):
    if not name or name == "default":
        fail(f"usage: {APP} edit <profile>")
    if not profile_exists(name):
        fail(f"no profile named '{name}'.")
    f = str(profile_dir(name) / "profile.json")
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if editor:
        return subprocess.call([*shlex.split(editor), f])
    opener = ["notepad.exe"] if IS_WIN else ["open", "-t"] if IS_MAC else ["xdg-open"]
    try:
        start_detached([*opener, f])
    except OSError:
        say(f"Edit {f}")
    return 0


# ------------------------------------------------------------------ Claude Desktop

def desktop_config_path():
    if os.environ.get("CLAUDE_PROFILE_DESKTOP_CONFIG"):
        return Path(os.environ["CLAUDE_PROFILE_DESKTOP_CONFIG"])
    cands = []
    if IS_WIN:
        local = os.environ.get("LOCALAPPDATA", "")
        cands += [Path(d) / "claude_desktop_config.json" for d in glob.glob(os.path.join(local, "Claude-Data", "claude_*"))]
        cands += [Path(d) / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json"
                  for d in glob.glob(os.path.join(local, "Packages", "Claude_*"))]
        cands.append(Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json")
    elif IS_MAC:
        cands.append(Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json")
    else:
        cands.append(Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "Claude" / "claude_desktop_config.json")
    return next((c for c in cands if c.is_file()), None)


def desktop_running():
    if os.environ.get("CLAUDE_PROFILE_DESKTOP_CONFIG"):
        return False
    try:
        if IS_WIN:
            n = powershell("@(Get-Process -Name claude -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "
                           "'*\\WindowsApps\\Claude_*' -or $_.Path -like '*\\AnthropicClaude\\*' }).Count")
            return int(n.strip() or 0) > 0
        args = ["-x", "Claude"] if IS_MAC else ["-if", "claude-desktop|/Claude.AppImage|/opt/Claude/"]
        return subprocess.run(["pgrep", *args], capture_output=True).returncode == 0
    except (OSError, ValueError, CliError):
        return False


def desktop_mcp_file(n):
    return HOME / "default-desktop-mcp.json" if n == "default" else profile_dir(n) / "desktop-mcp.json"


def cmd_desktop(sub, name=None):
    cfg_path = desktop_config_path()
    if not cfg_path:
        fail("could not find Claude Desktop (claude_desktop_config.json). Open Claude Desktop once, then retry.")
    current = read_text(DESKTOP_STATE)

    if not sub:
        say(f"Claude Desktop profile: {current or '(none chosen yet)'}")
        info(f"config: {cfg_path}")
        return 0
    if sub == "save":
        name = name or current
        if not name or not profile_exists(name):
            fail(f"usage: {APP} desktop save <profile>")
        write_json(desktop_mcp_file(name), (read_json(cfg_path, {}) or {}).get("mcpServers") or {})
        say(f"Saved Claude Desktop's current MCP servers into profile '{name}'.")
        return 0

    name = sub
    if not profile_exists(name):
        fail(f"no profile named '{name}'.")
    if desktop_running():
        fail("Claude Desktop is running. Quit it first so no Desktop session is cut off, then run this again. "
             "Terminal Claude Code sessions are not affected.")
    # Keep what was configured in Desktop for the outgoing profile.
    cmd_desktop("save", current if current and profile_exists(current) else "default")

    raw = cfg_path.read_text(encoding="utf-8-sig")
    # Backup lives with our data, not next to Desktop's config: a new file under
    # AppData written by Store Python would be hidden in its private folder.
    backup = HOME / "claude_desktop_config.backup.json"
    write_text(backup, raw)
    cfg = json.loads(raw)
    mcp = read_json(desktop_mcp_file(name), {}) or {}
    cfg.pop("mcpServers", None)
    if mcp:
        cfg["mcpServers"] = mcp
    write_json(cfg_path, cfg)
    write_text(DESKTOP_STATE, name)

    say(green(f"Claude Desktop now uses profile '{name}' ({len(mcp)} local MCP server(s)). Start Claude Desktop."))
    info("Desktop holds one sign-in at a time: if this customer uses a different Claude account, sign out and in once.")
    info("Cloud connectors (added on claude.ai) belong to the signed-in account, so they switch with the sign-in.")
    info(f"Previous config backed up to {backup}")
    return 0


# ------------------------------------------------------------------ import
# Reads an existing Claude Code setup (default ~/.claude + ~/.claude.json, or
# any config folder via --from), suggests which project folders belong to which
# customer, and copies them with their history, memory and MCP servers into
# profiles. The source is only read; 'import --apply <plan> --move' is the
# separate, guarded step that also takes them out of the source.

# Parent folders that say nothing about the customer (D:\work\acme -> "acme").
GENERIC_PARENTS = {"workspace", "workspaces", "work", "projects", "project", "repos", "repo", "src", "source",
                   "sources", "code", "dev", "git", "github", "gitlab", "clients", "customers", "documents",
                   "desktop", "sites", "www", "tmp", "temp", "home", "users"}
# Settings that are provider- or identity-specific, or handled by the plugin step.
SKIP_SETTINGS = {"env", "apiKeyHelper", "statusLine", "forceLoginMethod", "forceLoginOrgUUID", "enabledPlugins",
                 "extraKnownMarketplaces", "pluginConfigs", "awsAuthRefresh", "awsCredentialExport",
                 "otelHeadersHelper"}
CLAUDE_MODEL_SETTINGS = {"model", "advisorModel"}
CLAUDE_PROVIDERS = {"claude", "anthropic-api", "bedrock", "vertex"}
SHARED_KINDS = {"skills": "skills", "agents": "agents", "commands": "commands", "outputStyles": "output-styles"}


class Source:
    def __init__(self, from_dir=None):
        if from_dir:
            self.dir = Path(from_dir).expanduser().resolve()
            self.json = self.dir / ".claude.json"
            self.is_default = False
        else:
            self.dir = Path.home() / ".claude"
            self.json = Path.home() / ".claude.json"
            self.is_default = True
        if not self.dir.is_dir():
            fail(f"no Claude config folder at {self.dir}")

    def label(self):
        return "your default setup (~/.claude)" if self.is_default else str(self.dir)


def read_json_retry(path):
    """~/.claude.json is rewritten by every running Claude; retry if caught mid-write."""
    import time
    for _ in range(10):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return {}
        except ValueError:
            time.sleep(0.3)
    fail(f"{path} could not be read (it is being written); try again in a moment")


def canon_path(p):
    """One spelling per folder: Claude stores both D:/x and D:\\x."""
    p = os.path.normpath(str(p).replace("/", os.sep)) if IS_WIN else os.path.normpath(str(p))
    if IS_WIN and len(p) >= 2 and p[1] == ":":
        p = p[0].upper() + p[1:]
    marker = f"{os.sep}.claude{os.sep}worktrees{os.sep}"
    if marker in p:  # Claude worktrees belong to their repo
        p = p.split(marker)[0]
    return p


def is_under(child, parent):
    c, pa = path_key(child), path_key(parent)
    return c == pa or c.startswith(pa.rstrip(os.sep) + os.sep)


def encode_project(p):
    return re.sub(r"[^A-Za-z0-9]", "-", p)


def ignore_reason(p):
    low = p.lower().replace("\\", "/")
    home = str(Path.home())
    if path_key(p) == path_key(home) or os.path.dirname(p.rstrip(os.sep)) == p.rstrip(os.sep) or len(p.rstrip("\\/")) <= 2:
        return "home folder or drive root"
    if any(s in low + "/" for s in ("/appdata/local/temp/", "/tmp/", "/temp/", "scratch-workspaces", "remember-summarizer", "/var/folders/")):
        return "temporary folder"
    if (is_under(p, HOME) or is_under(p, Path.home() / ".claude-profile") or is_under(p, Path.home() / ".claudectl")
            or "claude-profile-demo" in low or "claudectl-demo" in low):
        return "claude-profile's own folders"
    if not os.path.isdir(p):
        return "folder no longer exists"
    return None


def transcript_cwd(folder):
    """Each transcript line records the folder the session ran in."""
    for f in sorted(folder.glob("*.jsonl")):
        try:
            with open(f, encoding="utf-8", errors="replace") as h:
                for i, line in enumerate(h):
                    if i > 50:
                        break
                    if '"cwd"' in line:
                        try:
                            cwd = json.loads(line).get("cwd")
                        except ValueError:
                            continue
                        if cwd:
                            return cwd
        except OSError:
            continue
    return None


def dir_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def old_switcher_leftovers():
    """Config folders, variables and profile lines left by earlier switcher scripts."""
    alt = []
    for d in Path.home().iterdir():
        if d.is_dir() and d.name.startswith(".claude") and d.name not in (".claude",) and not d.name.startswith((".claude-profile", ".claudectl")):
            if (d / ".claude.json").exists() or ((d / "settings.json").exists() and (d / "projects").is_dir()):
                alt.append(str(d))
    persistent = persistent_claude_vars()
    for scope in ("User", "Machine") if IS_WIN else ():
        try:
            v = powershell(f"[Environment]::GetEnvironmentVariable('CLAUDE_CONFIG_DIR','{scope}')").strip()
        except CliError:
            v = ""
        if v and os.path.isdir(v) and path_key(v) not in {path_key(a) for a in alt}:
            alt.append(v)
    return alt, persistent


def scan_setup(src):
    cj = read_json_retry(src.json) or {}
    projects = {}

    def proj(path):
        c = canon_path(path)
        if path_key(c) not in projects:
            projects[path_key(c)] = {"path": c, "keys": [], "historyDirs": [], "sessions": [], "prompts": 0,
                                     "localMcp": [], "bytes": 0, "ignored": ignore_reason(c)}
        return projects[path_key(c)]

    for key, entry in (cj.get("projects") or {}).items():
        p = proj(key)
        p["keys"].append(key)
        p["localMcp"] += list((entry or {}).get("mcpServers") or {})
    orphans = 0
    pdir = src.dir / "projects"
    if pdir.is_dir():
        known = {encode_project(k): k for k in (cj.get("projects") or {})}
        for d in sorted(x for x in pdir.iterdir() if x.is_dir()):
            cwd = transcript_cwd(d) or known.get(d.name)
            if not cwd:
                orphans += 1
                continue
            p = proj(cwd)
            p["historyDirs"].append(d.name)
            p["sessions"] += [f.stem for f in d.glob("*.jsonl")]
            p["bytes"] += dir_size(d)
    hist = src.dir / "history.jsonl"
    if hist.exists():
        for line in hist.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                pr = json.loads(line).get("project")
            except ValueError:
                continue
            if pr:
                proj(pr)["prompts"] += 1

    settings = read_json(src.dir / "settings.json", {}) or {}
    shared = {}
    for kind, folder in SHARED_KINDS.items():
        d = src.dir / folder
        shared[kind] = sorted(x.name for x in d.iterdir() if not x.name.startswith(".")) if d.is_dir() else []

    plugins = {"enabled": sorted(k for k, v in (settings.get("enabledPlugins") or {}).items() if v), "marketplaces": {}}
    km = read_json(src.dir / "plugins" / "known_marketplaces.json", {}) or {}
    for name, m in km.items():
        plugins["marketplaces"][name] = (m or {}).get("source")
    for name, m in (settings.get("extraKnownMarketplaces") or {}).items():
        plugins["marketplaces"].setdefault(name, (m or {}).get("source"))

    desktop = {}
    if src.is_default:
        cfg = desktop_config_path()
        if cfg:
            desktop = (read_json(cfg, {}) or {}).get("mcpServers") or {}
    alt, persistent = old_switcher_leftovers() if src.is_default else ([], [])
    acct = cj.get("oauthAccount") or {}
    repo_mcp = sorted(p["path"] for p in projects.values() if not p["ignored"] and os.path.exists(os.path.join(p["path"], ".mcp.json")))
    return {
        "projects": sorted(projects.values(), key=lambda p: path_key(p["path"])),
        "orphanHistory": orphans,
        "userMcp": cj.get("mcpServers") or {},
        "shared": shared,
        "claudeMd": (src.dir / "CLAUDE.md").exists(),
        "settings": {k: v for k, v in settings.items() if k not in SKIP_SETTINGS},
        "plugins": plugins,
        "desktopMcp": desktop,
        "altConfigDirs": alt,
        "persistentVars": persistent,
        "repoMcp": repo_mcp,
        "account": " / ".join(x for x in (acct.get("emailAddress"), acct.get("organizationName")) if x),
        "_claudeJson": cj,
    }


def depth_below_root(p):
    """D:\\Games -> 1, D:\\work\\acme -> 2; counted from the home folder when under it."""
    home = str(Path.home())
    base = home if is_under(p, home) else os.path.splitdrive(p)[0] + os.sep if IS_WIN else os.sep
    rel = os.path.relpath(p, base)
    return 0 if rel == "." else len(Path(rel).parts)


def profile_key(raw):
    key = re.sub(r"[^a-z0-9_-]+", "-", raw.lower()).strip("-_")[:40] or "customer"
    if not key[0].isalnum():
        key = "c" + key
    if key in RESERVED or key == "default":
        key += "-co"
    return key


def customer_key(path, all_paths=()):
    """The customer a folder most likely belongs to: D:\\work\\acme\\api -> acme, D:\\Games\\wow -> wow."""
    parent = os.path.dirname(path)
    pname = os.path.basename(parent)
    siblings = sum(1 for x in all_paths if is_under(x, parent) and path_key(x) != path_key(parent))
    grouping = (pname and pname.lower() not in GENERIC_PARENTS and path_key(parent) != path_key(Path.home())
                and os.path.dirname(parent) != parent and (siblings >= 2 or depth_below_root(parent) >= 2))
    raw, bind = (pname, parent) if grouping else (os.path.basename(path), path)
    return profile_key(raw), raw, bind


def owning_project(p, active):
    """A project nested in another one belongs to it when the outer one is a git repo or holds only this project."""
    for a in sorted((q for q in active if q is not p and is_under(p["path"], q["path"])), key=lambda q: len(q["path"])):
        inner = [x for x in active if x is not a and is_under(x["path"], a["path"])]
        if os.path.isdir(os.path.join(a["path"], ".git")) or len(inner) == 1:
            return a
    return None


def suggest_plan(inv, src):
    customers = {}
    active = [p for p in inv["projects"] if not p["ignored"]]
    owners = {p["path"]: owning_project(p, active) for p in active}
    tops = [p["path"] for p in active if owners[p["path"]] is None]
    keyed = {}
    for t in tops:
        key, raw, bind = customer_key(t, tops)
        # A folder that holds other projects (D:\Vaulted) is not bound: new repos under it
        # would silently default to it.
        if any(path_key(o) != path_key(t) and is_under(o, t) for o in tops):
            bind = None
        keyed[t] = (key, raw, bind)
    for p in active:
        top = p
        while owners[top["path"]] is not None:
            top = owners[top["path"]]
        key, raw, bind = keyed[top["path"]]
        c = customers.setdefault(key, {"label": raw, "provider": "claude", "projects": [], "bind": [], "mcp": [], "desktopMcp": []})
        c["projects"].append(p["path"])
        if p is top and bind and bind not in c["bind"]:
            c["bind"].append(bind)
    for name in inv["userMcp"]:
        for key, c in customers.items():
            if key.split("-")[0] in name.lower():
                c["mcp"].append(name)
    for name in inv["desktopMcp"]:
        for key, c in customers.items():
            if key.split("-")[0] in name.lower():
                c["desktopMcp"].append(name)
    return {
        "_help": ("Edit, then run: claude-profile import --apply <this file>. Each customer becomes a profile (name = key). "
                  "Remove a customer to keep its folders in your default setup. 'everyProfile' items are copied "
                  "into every customer profile in this plan. Nothing is removed from the source unless you add --move."),
        "source": None if src.is_default else str(src.dir),
        "customers": customers,
        "adopt": {d: profile_key(os.path.basename(d).replace(".claude-", "").lstrip(".") or "old") for d in inv["altConfigDirs"]},
        "everyProfile": {**{k: list(v) for k, v in inv["shared"].items()}, "claudeMd": inv["claudeMd"],
                         "settings": bool(inv["settings"]), "plugins": bool(inv["plugins"]["enabled"])},
    }


def fmt_size(n):
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{max(n, 1) // 1024 or 1} KB"


def print_scan(inv, src):
    say("")
    say(cyan(f"Your Claude setup: {src.label()}"))
    if inv["account"]:
        info(f"  signed in as {inv['account']} (this login stays in 'default'; it is never copied into a customer)")
    used = [p for p in inv["projects"] if not p["ignored"]]
    skipped = [p for p in inv["projects"] if p["ignored"]]
    say(f"\n  Project folders with history: {len(used)}")
    for p in used:
        extra = f", local MCP: {', '.join(p['localMcp'])}" if p["localMcp"] else ""
        info(f"    {p['path']}  ({len(p['sessions'])} sessions, {p['prompts']} prompts, {fmt_size(p['bytes'])}{extra})")
    if skipped:
        reasons = {}
        for p in skipped:
            reasons[p["ignored"]] = reasons.get(p["ignored"], 0) + 1
        info("    skipped, stay in default: " + ", ".join(f"{n} {r}" for r, n in reasons.items()))
    say(f"\n  MCP servers for all projects: {', '.join(inv['userMcp']) or 'none'}")
    for kind, names in inv["shared"].items():
        if names:
            say(f"  {kind}: {', '.join(names)}")
    if inv["claudeMd"]:
        say("  CLAUDE.md (your personal instructions)")
    if inv["plugins"]["enabled"]:
        say(f"  plugins: {', '.join(n.split('@')[0] for n in inv['plugins']['enabled'])}")
    if inv["settings"]:
        say(f"  preferences: {', '.join(sorted(inv['settings']))}")
    if inv["desktopMcp"]:
        say(f"  Claude Desktop MCP servers: {', '.join(inv['desktopMcp'])}")
    if inv["altConfigDirs"]:
        say(yellow(f"  config folders from an older switcher: {', '.join(inv['altConfigDirs'])}"))
    for v in inv["persistentVars"]:
        say(yellow(f"  {v} (left by an older switcher? remove it once your profiles work)"))
    if inv["repoMcp"]:
        info(f"  repos with their own .mcp.json (already isolated per repo): {len(inv['repoMcp'])}")
    info("  claude.ai connectors belong to the signed-in account and are not imported.")
    say("")


def ask_yes(q, default=True):
    a = ask(f"{q} [{'Y/n' if default else 'y/N'}] ").lower()
    return default if not a else a in ("y", "yes")


def pick_names(q, suggested, allowed):
    """Comma list of profile names; Enter keeps the suggestion, '-' means none."""
    a = ask(f"{q} [{', '.join(suggested) or 'none'}]: ")
    if not a:
        return suggested
    if a == "-":
        return []
    names = [x.strip() for x in a.split(",") if x.strip()]
    return [n for n in names if n in allowed or n == "*"] if allowed else names


def import_wizard(inv, src):
    plan = suggest_plan(inv, src)
    say(cyan("Step 1  Customers"))
    info("  Enter accepts the suggested profile name. Type another name to rename or merge, or 'k' to keep in default.")
    customers = {}
    for d, name in plan["adopt"].items():
        a = ask(f"\n  Turn the config folder {d} into profile [{name}] (Enter), another name, or 'k' to skip: ")
        if a.lower() == "k":
            plan["adopt"].pop(d)
            continue
        if a:
            plan["adopt"][d] = a.lower()
        customers.setdefault(plan["adopt"][d], {"label": plan["adopt"][d], "provider": "claude", "projects": [],
                                                "bind": [], "mcp": [], "desktopMcp": []})
    for key, c in plan["customers"].items():
        say("")
        for p in c["projects"]:
            info(f"    {p}")
        exists = " (existing profile)" if profile_exists(key) else ""
        a = ask(f"  -> profile [{key}]{exists}: ").strip().lower()
        if a == "k":
            continue
        name = a or key
        if not valid_name(name):
            say(yellow(f"  '{name}' is not a valid profile name; keeping these in default."))
            continue
        tgt = customers.setdefault(name, {**c, "projects": [], "bind": [], "mcp": [], "desktopMcp": []})
        tgt["projects"] += c["projects"]
        tgt["bind"] += [b for b in c["bind"] if b not in tgt["bind"]]
        tgt["mcp"] += c["mcp"]
        tgt["desktopMcp"] += c["desktopMcp"]
    if not customers:
        say("\nNothing assigned to a customer; nothing to import.")
        return None

    new = [n for n in customers if not profile_exists(n)]
    if new:
        providers = get_providers()
        say(cyan("\nStep 2  AI platform for each new profile"))
        for i, p in enumerate(providers, 1):
            info(f"    {i}) {p['label']}")
        for n in new:
            a = ask(f"  {n}: platform number [1 = Claude subscription, sign in on first start]: ")
            if a.isdigit() and 1 <= int(a) <= len(providers):
                customers[n]["provider"] = providers[int(a) - 1]["id"]

    names = list(customers)
    if inv["userMcp"] or inv["desktopMcp"]:
        say(cyan("\nStep 3  MCP servers"))
        info("  Comma-separated profile names. Enter keeps the suggestion; '-' keeps it only in default.")
        for kind, servers in (("mcp", inv["userMcp"]), ("desktopMcp", inv["desktopMcp"])):
            for s in servers:
                sug = [n for n in names if s in customers[n][kind]]
                chosen = pick_names(f"  {'Desktop ' if kind == 'desktopMcp' else ''}MCP '{s}' ->", sug, names)
                for n in names:
                    lst = [x for x in customers[n][kind] if x != s]
                    customers[n][kind] = lst + ([s] if n in chosen else [])

    say(cyan("\nStep 4  Things every new profile should get"))
    every = plan["everyProfile"]
    for kind in SHARED_KINDS:
        if every.get(kind):
            if not ask_yes(f"  Copy your {kind} ({', '.join(every[kind])}) into these profiles?"):
                every[kind] = []
    if every.get("claudeMd"):
        every["claudeMd"] = ask_yes("  Copy your personal CLAUDE.md instructions?")
    if every.get("settings"):
        every["settings"] = ask_yes(f"  Copy your preferences ({', '.join(sorted(inv['settings']))})?")
    if every.get("plugins"):
        every["plugins"] = ask_yes(f"  Install your plugins ({len(inv['plugins']['enabled'])}) in these profiles?")
    plan["customers"] = customers
    return plan


def plan_summary(plan, inv):
    by_path = {path_key(p["path"]): p for p in inv["projects"]}
    say(cyan("\nImport plan"))
    for n, c in plan["customers"].items():
        ps = [by_path.get(path_key(canon_path(x))) for x in c["projects"]]
        ps = [p for p in ps if p]
        size = sum(p["bytes"] for p in ps)
        state = "existing profile" if profile_exists(n) else f"new profile, {c.get('provider', 'claude')}"
        say(f"  {cyan(n)} ({state})")
        info(f"    {len(ps)} folder(s), {sum(len(p['sessions']) for p in ps)} sessions, {fmt_size(size)} history")
        if c.get("mcp"):
            info(f"    MCP: {', '.join(c['mcp'])}")
        if c.get("desktopMcp"):
            info(f"    Desktop MCP: {', '.join(c['desktopMcp'])}")
        for b in c.get("bind") or []:
            info(f"    bind: {b}")
    for d, n in (plan.get("adopt") or {}).items():
        info(f"  {d} -> {n} (whole folder, including its sign-in)")
    every = plan.get("everyProfile") or {}
    items = [k for k in SHARED_KINDS if every.get(k)] + [k for k in ("claudeMd", "settings", "plugins") if every.get(k)]
    if items:
        info(f"  every profile gets: {', '.join(items)}")


def copy_merge(src, dst):
    """Copies a file or folder without overwriting anything already in dst. Returns files copied."""
    src, dst = Path(src), Path(dst)
    if not src.exists():
        return 0
    if src.is_file():
        if dst.exists():
            return 0
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return 1
    n = 0
    for root, _, files in os.walk(src):
        rel = Path(root).relative_to(src)
        for f in files:
            n += copy_merge(Path(root) / f, dst / rel / f)
    return n


def minimal_env(name):
    """Enough environment to run 'claude plugin ...' against a profile, without its API key."""
    env = {k: v for k, v in os.environ.items() if not (SCRUB.match(k) and k.upper() not in SCRUB_KEEP)}
    env["CLAUDE_CONFIG_DIR"] = str(config_dir(name))
    return env


def marketplace_arg(source):
    if not isinstance(source, dict):
        return None
    kind = source.get("source")
    if kind == "github":
        return source.get("repo")
    if kind in ("git", "url"):
        return source.get("url")
    if kind in ("directory", "file"):
        return source.get("path")
    return None


def install_plugins(name, plugins, dry):
    exe = shutil.which("claude")
    if not exe:
        say(yellow("  claude not on PATH; skipping plugins"))
        return
    env = minimal_env(name)
    for m, source in plugins["marketplaces"].items():
        arg = marketplace_arg(source)
        if arg and not dry:
            subprocess.run([exe, "plugin", "marketplace", "add", arg], env=env, capture_output=True, text=True)
    for pl in plugins["enabled"]:
        if dry:
            info(f"    would install plugin {pl}")
            continue
        r = subprocess.run([exe, "plugin", "install", pl, "--scope", "user"], env=env, capture_output=True, text=True)
        ok = r.returncode == 0 or "already" in (r.stdout + r.stderr).lower()
        info(f"    plugin {pl.split('@')[0]}: {'installed' if ok else 'failed - ' + (r.stderr or r.stdout).strip()[:120]}")


def apply_plan(plan, src, inv, dry=False, plugins=True):
    by_path = {path_key(p["path"]): p for p in inv["projects"]}
    cj = inv["_claudeJson"]
    providers = {p["id"]: p for p in get_providers()}
    every = plan.get("everyProfile") or {}
    done = []

    for d, name in (plan.get("adopt") or {}).items():
        plan["customers"].setdefault(name, {"label": name, "provider": "claude", "projects": [], "bind": [], "mcp": [], "desktopMcp": []})

    for name, c in plan["customers"].items():
        if not valid_name(name):
            say(yellow(f"  skipping '{name}': not a valid profile name"))
            continue
        say(f"\n{cyan(name)}")
        if not profile_exists(name):
            prov = providers.get(c.get("provider") or "claude") or providers["claude"]
            if dry:
                info(f"    would create profile ({prov['id']})")
            else:
                create_profile(name, c.get("label") or name, prov, {})
                info(f"    created profile ({prov['id']})" + ("; set its key with: claude-profile key " + name if prov.get("secret") else ""))
        cfg = config_dir(name)
        prof = get_profile(name) if profile_exists(name) else {"provider": c.get("provider") or "claude"}

        for d, n in (plan.get("adopt") or {}).items():
            if n == name:
                k = 0 if dry else copy_merge(d, cfg)
                info(f"    {'would adopt' if dry else 'adopted'} {d} ({k} files)")
                if not dry:
                    s = read_json(cfg / "settings.json", {}) or {}
                    s["statusLine"] = status_line_setting(name)
                    write_json(cfg / "settings.json", s)

        # .claude.json: only MCP servers and the matched project entries; never the account or caches.
        pj = read_json(cfg / ".claude.json", {}) or {}
        pj.setdefault("mcpServers", {})
        pj.setdefault("projects", {})
        for s in c.get("mcp") or []:
            if s in inv["userMcp"]:
                pj["mcpServers"].setdefault(s, inv["userMcp"][s])
        sessions, hist_dirs, folders = set(), [], []
        for x in c.get("projects") or []:
            p = by_path.get(path_key(canon_path(x)))
            if not p:
                say(yellow(f"    not found in the source, skipped: {x}"))
                continue
            folders.append(p["path"])
            for key in p["keys"]:
                pj["projects"].setdefault(key, (cj.get("projects") or {}).get(key) or {})
            hist_dirs += p["historyDirs"]
            sessions.update(p["sessions"])
        files = 0
        if not dry:
            write_json(cfg / ".claude.json", pj)
            for hd in hist_dirs:
                files += copy_merge(src.dir / "projects" / hd, cfg / "projects" / hd)
            for sid in sessions:
                files += copy_merge(src.dir / "file-history" / sid, cfg / "file-history" / sid)
        lines = copy_history_lines(src, cfg, folders, dry)
        info(f"    {'would copy' if dry else 'copied'} {len(folders)} folder(s): {len(sessions)} sessions, {lines} prompts"
             + (f", {files} files" if files else ""))
        if c.get("mcp"):
            info(f"    MCP servers: {', '.join(c['mcp'])}")

        for kind, folder in SHARED_KINDS.items():
            for item in every.get(kind) or []:
                if not dry:
                    copy_merge(src.dir / folder / item, cfg / folder / item)
            if every.get(kind):
                info(f"    {kind}: {', '.join(every[kind])}")
        if every.get("claudeMd") and not dry:
            copy_merge(src.dir / "CLAUDE.md", cfg / "CLAUDE.md")
        if every.get("settings"):
            s = read_json(cfg / "settings.json", {}) or {}
            for k, v in inv["settings"].items():
                if k in CLAUDE_MODEL_SETTINGS and prof.get("provider") not in CLAUDE_PROVIDERS:
                    continue
                s.setdefault(k, v)
            if not dry:
                write_json(cfg / "settings.json", s)
            info("    preferences copied")
        if c.get("desktopMcp"):
            dm = read_json(desktop_mcp_file(name), {}) or {}
            for s in c["desktopMcp"]:
                if s in inv["desktopMcp"]:
                    dm.setdefault(s, inv["desktopMcp"][s])
            if not dry:
                write_json(desktop_mcp_file(name), dm)
            info(f"    Desktop MCP servers: {', '.join(c['desktopMcp'])} (active after 'claude-profile desktop {name}')")
        if every.get("plugins") and plugins and inv["plugins"]["enabled"]:
            info("    plugins:")
            install_plugins(name, inv["plugins"], dry)
        for b in c.get("bind") or []:
            if dry:
                info(f"    would bind {b}")
            else:
                cmd_bind(name, b)
        done.append(name)
    return done


def copy_history_lines(src, cfg, folders, dry):
    hist = src.dir / "history.jsonl"
    if not folders or not hist.exists():
        return 0
    dst = cfg / "history.jsonl"
    seen = set()
    if dst.exists():
        for line in dst.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                o = json.loads(line)
                seen.add((o.get("sessionId"), o.get("timestamp"), o.get("display")))
            except ValueError:
                pass
    out = []
    for line in hist.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            o = json.loads(line)
        except ValueError:
            continue
        pr = o.get("project")
        if pr and any(is_under(canon_path(pr), f) for f in folders):
            k = (o.get("sessionId"), o.get("timestamp"), o.get("display"))
            if k not in seen:
                seen.add(k)
                out.append(line)
    if out and not dry:
        cfg.mkdir(parents=True, exist_ok=True)
        with open(dst, "a", encoding="utf-8") as h:
            h.write("\n".join(out) + "\n")
    return len(out)


def claude_code_running():
    try:
        if IS_WIN:
            n = powershell("@(Get-Process -Name claude -ErrorAction SilentlyContinue | Where-Object { $_.Path -and "
                           "$_.Path -notlike '*\\WindowsApps\\Claude_*' -and $_.Path -notlike '*\\AnthropicClaude\\*' }).Count")
            return int(n.strip() or 0) > 0
        return subprocess.run(["pgrep", "-x", "claude"], capture_output=True).returncode == 0
    except (OSError, ValueError, CliError):
        return True


def refuse_move_while_running():
    if claude_code_running():
        fail("Claude Code is running (a terminal session or Desktop's Code tab). It rewrites its config while it runs, "
             "so close every Claude Code session first, then run this again. Nothing was changed.")


def move_out_of_source(plan, src, inv):
    """Takes imported customer data out of the source. Everything removed goes to a backup folder."""
    by_path = {path_key(p["path"]): p for p in inv["projects"]}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = HOME / "import-backups" / stamp
    backup.mkdir(parents=True)
    for f in (src.json, src.dir / "settings.json", src.dir / "history.jsonl"):
        if f.exists():
            shutil.copy2(f, backup / f.name)
    cj = read_json_retry(src.json) or {}
    moved_mcp = {s for c in plan["customers"].values() for s in (c.get("mcp") or [])}
    folders, hist_dirs, sessions = [], [], set()
    for c in plan["customers"].values():
        for x in c.get("projects") or []:
            p = by_path.get(path_key(canon_path(x)))
            if p:
                folders.append(p["path"])
                hist_dirs += p["historyDirs"]
                sessions.update(p["sessions"])
                for key in p["keys"]:
                    (cj.get("projects") or {}).pop(key, None)
    for s in moved_mcp:
        (cj.get("mcpServers") or {}).pop(s, None)
    write_json(src.json, cj)
    for hd in hist_dirs:
        if (src.dir / "projects" / hd).exists():
            shutil.move(str(src.dir / "projects" / hd), str(backup / "projects" / hd))
    for sid in sessions:
        if (src.dir / "file-history" / sid).exists():
            shutil.move(str(src.dir / "file-history" / sid), str(backup / "file-history" / sid))
    hist = src.dir / "history.jsonl"
    if hist.exists():
        keep = []
        for line in hist.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                pr = json.loads(line).get("project")
            except ValueError:
                pr = None
            if not (pr and any(is_under(canon_path(pr), f) for f in folders)):
                keep.append(line)
        hist.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    say(green(f"Moved out of {src.label()}: {len(folders)} folder(s), {len(moved_mcp)} MCP server(s)."))
    info(f"Everything removed is kept in {backup}")
    info("Note: Claude Desktop's Code tab uses your default setup, so it no longer shows the moved history.")


def cmd_import(opt):
    src = Source(opt.get("from"))
    inv = scan_setup(src)
    if opt.get("scan"):
        print_scan(inv, src)
        return 0
    if opt.get("plan"):
        plan = suggest_plan(inv, src)
        write_json(opt["plan"], plan)
        say(f"Suggested plan written to {opt['plan']}. Edit it, then run: {APP} import --apply {opt['plan']}")
        return 0
    dry = bool(opt.get("dry-run"))
    if opt.get("move") and not dry:
        refuse_move_while_running()
    if opt.get("apply"):
        plan = read_json(opt["apply"])
        if not isinstance(plan, dict) or "customers" not in plan:
            fail(f"{opt['apply']} is not an import plan")
        plan_file = Path(opt["apply"])
    else:
        print_scan(inv, src)
        plan = import_wizard(inv, src)
        if not plan:
            return 0
        plan_summary(plan, inv)
        say("")
        if not dry and not ask_yes("Import now? This only copies; your current setup is not changed."):
            say("Cancelled.")
            return 1
        plan_file = HOME / "import-plans" / f"import-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        write_json(plan_file, plan)
    done = apply_plan(plan, src, inv, dry=dry, plugins=not opt.get("no-plugins"))
    if dry:
        say(gray("\nDry run: nothing was written."))
        return 0
    say(green(f"\nImported into: {', '.join(done) or 'nothing'}."))
    if opt.get("move"):
        move_out_of_source(plan, src, inv)
    else:
        info("Your default setup still has everything. To take the imported customers out of it as well,")
        info(f"close all Claude Code sessions and run:  {APP} import --apply \"{plan_file}\" --move")
    return 0


# ------------------------------------------------------------------ install

def bin_dir():
    return Path(os.environ.get("CLAUDE_PROFILE_BIN") or Path.home() / ".local" / "bin")


def shim_path():
    return bin_dir() / (f"{APP}.cmd" if IS_WIN else APP)


def app_copy():
    return HOME / "app" / "claude_profile.py"


def start_menu_shortcut():
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Claude Profiles.lnk"


def ps_quote(s):
    return "'" + str(s).replace("'", "''") + "'"


def refresh_status_lines():
    """Profiles created before an install point their status line at the old location."""
    for n in profile_names()[1:]:
        f = config_dir(n) / "settings.json"
        s = read_json(f)
        if isinstance(s, dict):
            s["statusLine"] = status_line_setting(n)
            write_json(f, s)


def cmd_install():
    """Puts 'claude-profile' on PATH: the binary itself, or this script plus a launcher.
    Installed with pip/pipx/uv, the command already exists; only the extras are set up."""
    global SELF, SELF_COPY
    launcher = None
    if FROZEN:
        launcher = bin_dir() / (f"{APP}.exe" if IS_WIN else APP)
        launcher.parent.mkdir(parents=True, exist_ok=True)
        if path_key(sys.executable) != path_key(launcher):
            if launcher.exists():
                old = launcher.with_name(launcher.name + ".old")
                old.unlink(missing_ok=True)
                launcher.rename(old)  # a running .exe can be renamed, not overwritten
            shutil.copy2(sys.executable, launcher)
        menu_target, menu_args = launcher, "menu"
    elif __package__:
        found = shutil.which(APP)
        menu_target, menu_args = (found, "menu") if found else (sys.executable, f"-m {MODULE} menu")
        say(green(f"{APP} is installed as a Python package" + (f": {found}" if found else "")))
    else:
        target = app_copy()
        target.parent.mkdir(parents=True, exist_ok=True)
        if path_key(SELF) != path_key(target):
            shutil.copyfile(SELF, target)
        SELF, SELF_COPY = str(target), True
        launcher = shim_path()
        launcher.parent.mkdir(parents=True, exist_ok=True)
        if IS_WIN:
            launcher.write_text(f'@"{sys.executable}" "{target}" %*\r\n', encoding="ascii")
        else:
            launcher.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(target))} "$@"\n')
            launcher.chmod(0o755)
        menu_target, menu_args = sys.executable, f'"{target}" menu'
    refresh_status_lines()
    if launcher:
        say(green(f"{APP} installed: {launcher}"))

    on_path = any(p and path_key(p) == path_key(bin_dir()) for p in os.environ.get("PATH", "").split(os.pathsep))
    if launcher and not on_path:
        if IS_WIN:
            d = ps_quote(bin_dir())
            powershell(f"$d={d}; $p=[Environment]::GetEnvironmentVariable('Path','User'); if (-not (($p -split ';') -contains $d)) "
                       "{ [Environment]::SetEnvironmentVariable('Path', ($p.TrimEnd(';') + ';' + $d).TrimStart(';'), 'User') }")
            say(f"Added {bin_dir()} to your user PATH. Open a new terminal to use '{APP}'.")
        else:
            say(yellow(f"{bin_dir()} is not on your PATH. Add this line to ~/.zshrc or ~/.bashrc:"))
            say('  export PATH="$HOME/.local/bin:$PATH"')
    if IS_WIN:
        try:
            powershell(f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({ps_quote(start_menu_shortcut())}); "
                       f"$s.TargetPath={ps_quote(menu_target)}; $s.Arguments={ps_quote(menu_args)}; "
                       "$s.WorkingDirectory=$HOME; $s.Description='Pick a Claude customer profile'; $s.Save()")
            say('Start menu: "Claude Profiles" opens a picker.')
        except CliError:
            pass
    say("")
    say(f"  {APP} add acme     add your first customer")
    say(f"  {APP} help         all commands")
    return 0


def cmd_uninstall():
    binary = bin_dir() / (f"{APP}.exe" if IS_WIN else APP)
    for f in (shim_path(), app_copy(), start_menu_shortcut() if IS_WIN else None,
              binary if FROZEN or not IS_WIN else None):
        if f and f.exists() and not (FROZEN and IS_WIN and path_key(f) == path_key(sys.executable)):
            f.unlink()
    if FROZEN and IS_WIN and binary.exists():
        say(f"Delete {binary} once this window is closed (Windows can't delete a running program).")
    if __package__ and not FROZEN:
        say(f"Installed with pip/pipx/uv? Remove the package with:  pipx uninstall {DIST}  (or pip uninstall {DIST})")
    say(f"{APP} removed. Your profiles are still in {PROFILES} (delete that folder to remove them too).")
    return 0


# ------------------------------------------------------------------ menu, doctor, help

def cmd_menu():
    while True:
        names = profile_names()
        say(f"\n{cyan('Claude profiles')}")
        for i, n in enumerate(names, 1):
            p = get_profile(n)
            say(f"  {i}) {n.ljust(14)} {p['label']}  - {p.get('summary', '')}")
        say("  a) add a customer    q) quit")
        c = ask("Choose: ")
        if c in ("", "q"):
            return 0
        try:
            if c == "a":
                cmd_add(None, {"set": {}})
                continue
            if not c.isdigit() or not 1 <= int(c) <= len(names):
                continue
            n = names[int(c) - 1]
            a = ask("  1) open in a new terminal tab   2) make active   3) switch Claude Desktop to it   4) back: ")
            if a == "1":
                cmd_tab(n, None)
            elif a == "2":
                cmd_use(n)
            elif a == "3":
                cmd_desktop(n)
        except CliError as e:
            say(red(f"{APP}: {e}"))


def persistent_claude_vars():
    """Variables set user- or machine-wide (registry, shell rc files) by earlier scripts."""
    found = []
    if IS_WIN:
        for scope in ("User", "Machine"):
            try:
                out = powershell(f"[Environment]::GetEnvironmentVariables('{scope}').Keys -join \"`n\"")
            except CliError:
                continue
            found += [f"{scope}-wide variable {k} is set" for k in out.split() if SCRUB.match(k)]
        docs = Path.home() / "Documents"
        pat = re.compile(r"\$env:(ANTHROPIC_\w+|CLAUDE_CONFIG_DIR|CLAUDE_CODE_\w+)\s*=", re.I)
        for f in (docs / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1", docs / "WindowsPowerShell" / "profile.ps1",
                  docs / "PowerShell" / "Microsoft.PowerShell_profile.ps1", docs / "PowerShell" / "profile.ps1"):
            found += [f"{f} sets {m}" for m in sorted(set(pat.findall(read_text(f))))]
        return found
    pat = re.compile(r"^\s*export\s+(ANTHROPIC_\w+|CLAUDE_CONFIG_DIR|CLAUDE_CODE_\w+)", re.M)
    for rc in (".bashrc", ".bash_profile", ".profile", ".zshrc", ".zprofile", ".zshenv"):
        found += [f"~/{rc} exports {m}" for m in pat.findall(read_text(Path.home() / rc))]
    return found


def cmd_doctor():
    def ok(m): say(f"  {green('ok  ')}  {m}")
    def warn(m): say(f"  {yellow('warn')}  {m}")
    c = shutil.which("claude")
    ok(f"claude found: {c}") if c else warn("claude not on PATH")
    ok(f"python {sys.version.split()[0]}: {sys.executable}")
    if IS_WIN:
        ok("Windows Terminal found (tabs)") if shutil.which("wt") else warn("Windows Terminal not found; tabs open as new windows")
    elif IS_MAC:
        ok("tabs open in Terminal.app; API keys in the login Keychain")
    else:
        t = next((t for t in ("gnome-terminal", "konsole", "kitty", "wezterm", "alacritty", "xfce4-terminal", "x-terminal-emulator") if shutil.which(t)), None)
        ok(f"terminal for tabs: {t}") if t else warn("no supported terminal found for tabs")
        ok("keyring: secret-tool") if shutil.which("secret-tool") else warn("secret-tool not found; API keys fall back to files readable only by you (install libsecret-tools)")
    d = desktop_config_path()
    ok(f"Claude Desktop config: {d}") if d else warn("Claude Desktop config not found")
    for m in persistent_claude_vars():
        warn(f"{m}; plain 'claude' still sees it ({APP} overrides it)")
    managed = ([r"C:\ProgramData\ClaudeCode\managed-settings.json", r"C:\ProgramData\ClaudeCode\managed-mcp.json",
                r"C:\Program Files\ClaudeCode\managed-settings.json"] if IS_WIN else
               ["/Library/Application Support/ClaudeCode/managed-settings.json",
                "/Library/Application Support/ClaudeCode/managed-mcp.json"] if IS_MAC else
               ["/etc/claude-code/managed-settings.json", "/etc/claude-code/managed-mcp.json"])
    for m in managed:
        if os.path.exists(m):
            warn(f"{m} applies to every profile")
    ok(f"profiles stored in {PROFILES}")
    return 0


HELP = """claude-profile - Claude profiles per customer

  claude-profile                         start Claude Code with the profile for this folder (or the active one)
  claude-profile <profile> [args...]     start Claude Code with that profile; args go to claude
                                    e.g.  claude-profile acme --resume    claude-profile acme mcp add jira -- npx ...
  claude-profile mcp|plugin ...          claude subcommand, run in this folder's profile
  claude-profile tab [profile] [dir]     open a new terminal tab locked to a profile
  claude-profile shell [profile]         open a shell locked to a profile (type 'claude' as often as you like)
  claude-profile add [name]              add a customer profile (guided)
  claude-profile list                    show profiles, bindings, active and Desktop profile
  claude-profile use [profile]           set the active profile for new sessions
  claude-profile bind <profile> [dir]    tie a folder (and subfolders) to a profile
  claude-profile unbind [dir]            remove the tie
  claude-profile status                  which profile applies here and why
  claude-profile desktop [profile]       switch Claude Desktop's MCP servers to a profile (Desktop must be closed)
  claude-profile desktop save [profile]  store Desktop's current MCP servers in a profile
  claude-profile key <profile>           replace a stored API key
  claude-profile edit <profile>          open the profile file (models, env)
  claude-profile remove <profile>        delete a profile and all its data
  claude-profile import                  sort your existing Claude setup into customer profiles (guided, copies only)
  claude-profile import --scan           just show what is in your current setup
  claude-profile import --plan FILE      write a suggested plan to edit;  --apply FILE [--move] [--dry-run] to run it
                                    (--from DIR imports another config folder; --move also takes it out of the source)
  claude-profile menu                    pick from a menu
  claude-profile doctor                  check the setup
  claude-profile install | uninstall     put claude-profile on your PATH / take it off (profiles are kept)

Running sessions are never touched: a profile is chosen when a session starts."""


# ------------------------------------------------------------------ dispatch

def dispatch(argv):
    cmd, rest = (argv[0], argv[1:]) if argv else ("", [])
    opt, pos = parse_options(rest)
    arg = lambda i: pos[i] if i < len(pos) else None  # noqa: E731
    commands = {
        "run": lambda: cmd_run(rest[0] if rest else None, rest[1:]),
        "add": lambda: cmd_add(arg(0), opt),
        "list": cmd_list, "ls": cmd_list,
        "use": lambda: cmd_use(arg(0)),
        "bind": lambda: cmd_bind(arg(0), arg(1)),
        "unbind": lambda: cmd_unbind(arg(0)),
        "status": cmd_status,
        "tab": lambda: cmd_tab(arg(0), arg(1)),
        "shell": lambda: cmd_shell(arg(0), bool(opt.get("start"))),
        "desktop": lambda: cmd_desktop(arg(0), arg(1)),
        "key": lambda: cmd_key(arg(0)),
        "edit": lambda: cmd_edit(arg(0)),
        "remove": lambda: cmd_remove(arg(0)), "rm": lambda: cmd_remove(arg(0)),
        "menu": cmd_menu,
        "doctor": cmd_doctor,
        "import": lambda: cmd_import(opt),
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "statusline": lambda: cmd_statusline(arg(0)),
        "version": lambda: say(f"{APP} {VERSION} (python {sys.version.split()[0]}, {sys.platform})") or 0,
        "--version": lambda: say(f"{APP} {VERSION}") or 0,
        "help": lambda: say(HELP) or 0, "-h": lambda: say(HELP) or 0, "--help": lambda: say(HELP) or 0,
    }
    if cmd in commands:
        return commands[cmd]()
    if cmd and profile_exists(cmd):
        return cmd_run(cmd, rest)
    if not cmd or cmd in CLAUDE_SUBCOMMANDS or cmd.startswith("-"):
        return cmd_run(None, argv)
    fail(f"unknown command or profile '{cmd}'. Run '{APP} help' or '{APP} list'.")


def main():
    _enable_vt()
    try:
        code = dispatch(sys.argv[1:])
    except CliError as e:
        print(red(f"{APP}: {e}"), file=sys.stderr)
        code = 1
    except KeyboardInterrupt:
        print()
        code = 130
    sys.exit(code or 0)


if __name__ == "__main__":
    main()
