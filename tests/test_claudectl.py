import importlib
import json
import os
import re
import sys

import pytest


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """claudectl with its data folder in a temp dir and no inherited Claude settings."""
    monkeypatch.setenv("CLAUDECTL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLAUDECTL_DESKTOP_CONFIG", raising=False)
    import claudectl.cli as mod
    mod = importlib.reload(mod)
    return mod


def add(cli, name, provider="claude", *extra):
    return cli.dispatch(["add", name, "--provider", provider, "--no-bind", *extra])


# ------------------------------------------------------------------ profiles

def test_version_matches_package(cli):
    import claudectl
    assert cli.VERSION == claudectl.__version__


def test_add_and_list(cli, capsys):
    assert add(cli, "acme", "claude", "--label", "ACME Corp") == 0
    assert cli.profile_names() == ["default", "acme"]
    p = cli.get_profile("acme")
    assert p["label"] == "ACME Corp" and p["provider"] == "claude"
    settings = json.loads((cli.config_dir("acme") / "settings.json").read_text())
    assert settings["forceLoginMethod"] == "claudeai"
    assert "forceLoginOrgUUID" not in settings  # empty org is dropped
    assert "statusline acme" in settings["statusLine"]["command"]
    cli.dispatch(["list"])
    assert "acme" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["list", "add", "default", "Bad Name", "-x", ""])
def test_rejects_bad_names(cli, name):
    with pytest.raises(cli.CliError):
        add(cli, name)


def test_secret_round_trip(cli):
    try:
        add(cli, "ds", "deepseek", "--key", "sk-test-123")
    except cli.CliError as e:  # e.g. a locked macOS keychain on a CI runner
        pytest.skip(f"no usable secret store here: {e}")
    p = cli.get_profile("ds")
    assert "sk-test-123" not in json.dumps(p)
    assert cli.unprotect_secret("ds", p["secret"]) == "sk-test-123"
    assert p["env"]["ANTHROPIC_MODEL"] == "deepseek-flash[1m]"


def test_profile_env_is_isolated(cli, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://parent")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent-session")
    monkeypatch.setenv("OPENAI_API_KEY", "leak")
    monkeypatch.setenv("CLAUDE_CODE_GIT_BASH_PATH", "keep-me")
    add(cli, "acme")
    env = cli.profile_env(cli.get_profile("acme"))
    assert "ANTHROPIC_BASE_URL" not in env and "CLAUDE_CODE_SESSION_ID" not in env and "OPENAI_API_KEY" not in env
    assert env["CLAUDE_CODE_GIT_BASH_PATH"] == "keep-me"
    assert env["CLAUDE_CONFIG_DIR"] == str(cli.config_dir("acme"))
    assert env["CLAUDECTL_PROFILE"] == "acme"
    default_env = cli.profile_env(cli.get_profile("default"))
    assert "CLAUDE_CONFIG_DIR" not in default_env and "ANTHROPIC_BASE_URL" not in default_env


def test_bindings_pick_the_deepest_folder(cli, tmp_path):
    outer, inner = tmp_path / "work" / "acme", tmp_path / "work" / "acme" / "special"
    (inner / "src").mkdir(parents=True)
    add(cli, "acme")
    add(cli, "globex")
    cli.dispatch(["bind", "acme", str(outer)])
    cli.dispatch(["bind", "globex", str(inner)])
    assert cli.resolve_here(str(outer / "x"))[0] == "acme"
    assert cli.resolve_here(str(inner / "src"))[0] == "globex"
    assert cli.resolve_here(str(tmp_path))[0] == "default"
    cli.dispatch(["use", "acme"])
    assert cli.resolve_here(str(tmp_path))[0] == "acme"


def test_parse_options_keeps_claude_args():
    from claudectl.cli import parse_options
    opt, pos = parse_options(["acme", "--provider", "deepseek", "--set", "model=x", "--bind"])
    assert pos == ["acme"] and opt["provider"] == "deepseek" and opt["set"] == {"model": "x"} and opt["bind"]


# ------------------------------------------------------------------ Claude Desktop

def test_desktop_switch_round_trip(cli, tmp_path, monkeypatch):
    cfg = tmp_path / "desktop.json"
    cfg.write_text(json.dumps({"preferences": {"a": 1}, "mcpServers": {"personal": {"command": "x"}}}))
    monkeypatch.setenv("CLAUDECTL_DESKTOP_CONFIG", str(cfg))
    add(cli, "acme")
    add(cli, "globex")
    cli.dispatch(["desktop", "acme"])
    assert "mcpServers" not in json.loads(cfg.read_text())
    data = json.loads(cfg.read_text())
    data["mcpServers"] = {"acme-jira": {"command": "jira"}}
    cfg.write_text(json.dumps(data))
    cli.dispatch(["desktop", "globex"])
    assert "mcpServers" not in json.loads(cfg.read_text())
    cli.dispatch(["desktop", "acme"])
    assert list(json.loads(cfg.read_text())["mcpServers"]) == ["acme-jira"]
    cli.dispatch(["desktop", "default"])
    final = json.loads(cfg.read_text())
    assert list(final["mcpServers"]) == ["personal"] and final["preferences"] == {"a": 1}


# ------------------------------------------------------------------ import

def make_source(base):
    """A fake Claude config folder (CLAUDE_CONFIG_DIR layout)."""
    work = base / "work"
    api, web, portal = work / "acme" / "api", work / "acme" / "web", work / "globex-portal"
    wt = api / ".claude" / "worktrees" / "wt1"
    scratch = base / "scratch-workspaces" / "s1"
    for d in (api, web, portal, wt, scratch):
        d.mkdir(parents=True)
    src = base / "src"
    src.mkdir()
    fwd = lambda p: str(p).replace("\\", "/")
    enc = lambda p: re.sub(r"[^A-Za-z0-9]", "-", str(p))
    (src / ".claude.json").write_text(json.dumps({
        "oauthAccount": {"emailAddress": "me@example.com"},
        "machineID": "SECRET-MACHINE",
        "mcpServers": {"acme-jira": {"command": "jira"}, "shared-fs": {"command": "fs"}},
        "projects": {
            fwd(api): {"allowedTools": ["Bash"]},
            str(api): {"hasTrustDialogAccepted": True},
            str(wt): {},
            fwd(web): {"mcpServers": {"acme-web-db": {"command": "db"}}},
            fwd(portal): {},
            fwd(scratch): {},
        },
    }))
    (src / ".credentials.json").write_text("MY-OWN-LOGIN")
    sessions = {api: "s-api", web: "s-web", portal: "s-portal", wt: "s-wt", scratch: "s-tmp"}
    for folder, sid in sessions.items():
        d = src / "projects" / enc(folder)
        (d / "memory").mkdir(parents=True)
        (d / f"{sid}.jsonl").write_text(json.dumps({"cwd": str(folder), "sessionId": sid}) + "\n")
        (d / "memory" / "MEMORY.md").write_text("memory")
        (src / "file-history" / sid).mkdir(parents=True)
        (src / "file-history" / sid / "f").write_text("x")
    lines = [json.dumps({"display": f"p{i}", "project": str(f), "sessionId": s, "timestamp": i})
             for i, (f, s) in enumerate(sessions.items())]
    (src / "history.jsonl").write_text("\n".join(lines) + "\n")
    (src / "skills" / "myskill").mkdir(parents=True)
    (src / "skills" / "myskill" / "SKILL.md").write_text("skill")
    (src / "settings.json").write_text(json.dumps({"model": "sonnet", "theme": "dark", "env": {"X": "1"}}))
    return src, api, web, portal


@pytest.fixture
def source(cli, tmp_path, monkeypatch):
    # pytest's tmp_path is itself a temp folder; only the fixture's scratch folder should count as one.
    monkeypatch.setattr(cli, "ignore_reason",
                        lambda p: "temporary folder" if "scratch-workspaces" in p
                        else None if os.path.isdir(p) else "folder no longer exists")
    return make_source(tmp_path)


def test_ignore_reason_rules(cli, tmp_path):
    assert cli.ignore_reason(str(tmp_path.home())) == "home folder or drive root"
    assert cli.ignore_reason(os.path.join(str(tmp_path), "scratch-workspaces", "x")) == "temporary folder"
    assert cli.ignore_reason(str(tmp_path.home() / "definitely-missing-folder-xyz")) == "folder no longer exists"


def test_canon_path_folds_worktrees_and_spellings(cli):
    base = os.path.join(os.sep, "w", "acme")
    assert cli.canon_path(os.path.join(base, ".claude", "worktrees", "wt1")) == os.path.normpath(base)
    if sys.platform == "win32":
        assert cli.canon_path("d:/w/acme") == cli.canon_path("D:\\w\\acme")


def test_scan_and_suggest(cli, source):
    src_dir, api, web, portal = source
    src = cli.Source(str(src_dir))
    inv = cli.scan_setup(src)
    used = {p["path"]: p for p in inv["projects"] if not p["ignored"]}
    assert set(used) == {str(api), str(web), str(portal)}
    assert sorted(used[str(api)]["sessions"]) == ["s-api", "s-wt"]  # worktree folded in
    plan = cli.suggest_plan(inv, src)
    assert set(plan["customers"]) == {"acme", "globex-portal"}
    assert plan["customers"]["acme"]["bind"] == [str(api.parent)]
    assert plan["customers"]["acme"]["mcp"] == ["acme-jira"]


def test_import_copies_without_identity_and_is_idempotent(cli, source, tmp_path):
    src_dir, api, web, portal = source
    plan_file = tmp_path / "plan.json"
    cli.dispatch(["import", "--from", str(src_dir), "--plan", str(plan_file)])
    plan = json.loads(plan_file.read_text())
    plan["customers"]["globex-portal"]["provider"] = "deepseek"
    plan_file.write_text(json.dumps(plan))
    before = (src_dir / ".claude.json").read_text()

    cli.dispatch(["import", "--from", str(src_dir), "--apply", str(plan_file), "--no-plugins"])

    cfg = cli.config_dir("acme")
    pj = json.loads((cfg / ".claude.json").read_text())
    assert list(pj["mcpServers"]) == ["acme-jira"]
    assert "acme-web-db" in json.dumps(pj["projects"])
    assert "oauthAccount" not in pj and "SECRET-MACHINE" not in json.dumps(pj)
    assert not (cfg / ".credentials.json").exists()
    assert len(list((cfg / "projects").iterdir())) == 3  # api, worktree, web
    assert (cfg / "skills" / "myskill" / "SKILL.md").exists()
    settings = json.loads((cfg / "settings.json").read_text())
    assert settings["model"] == "sonnet" and "env" not in settings
    assert "model" not in json.loads((cli.config_dir("globex-portal") / "settings.json").read_text())
    assert cli.get_profile("globex-portal")["env"]["ANTHROPIC_MODEL"] == "deepseek-flash[1m]"
    assert cli.resolve_here(str(web))[0] == "acme"
    assert (src_dir / ".claude.json").read_text() == before  # source untouched

    lines = (cfg / "history.jsonl").read_text().splitlines()
    cli.dispatch(["import", "--from", str(src_dir), "--apply", str(plan_file), "--no-plugins"])
    assert (cfg / "history.jsonl").read_text().splitlines() == lines


def test_move_refuses_while_claude_runs(cli, source, tmp_path, monkeypatch):
    src_dir = source[0]
    plan_file = tmp_path / "plan.json"
    cli.dispatch(["import", "--from", str(src_dir), "--plan", str(plan_file)])
    monkeypatch.setattr(cli, "claude_code_running", lambda: True)
    with pytest.raises(cli.CliError):
        cli.dispatch(["import", "--from", str(src_dir), "--apply", str(plan_file), "--move", "--no-plugins"])
    assert not cli.profile_exists("acme")  # refused before copying anything


def test_move_takes_customers_out_of_source(cli, source, tmp_path, monkeypatch):
    src_dir = source[0]
    plan_file = tmp_path / "plan.json"
    cli.dispatch(["import", "--from", str(src_dir), "--plan", str(plan_file)])
    monkeypatch.setattr(cli, "claude_code_running", lambda: False)
    cli.dispatch(["import", "--from", str(src_dir), "--apply", str(plan_file), "--move", "--no-plugins"])
    cj = json.loads((src_dir / ".claude.json").read_text())
    assert list(cj["mcpServers"]) == ["shared-fs"]
    assert "oauthAccount" in cj
    assert [k for k in cj["projects"] if "scratch-workspaces" not in k] == []
    assert len(list((src_dir / "projects").iterdir())) == 1  # only the scratch folder's history remains
    backups = list((cli.HOME / "import-backups").iterdir())
    assert len(backups) == 1 and (backups[0] / ".claude.json").exists()
