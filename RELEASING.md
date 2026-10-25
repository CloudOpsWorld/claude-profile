# Releasing claude-profile

Publishing is fully automated via GitHub Actions: pushing a new version to `main`
triggers a release.

## Workflows

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| `.github/workflows/ci.yml` | push / PR to `main`/`master` | Test matrix (Ubuntu + macOS + Windows × Python 3.10–3.13), advisory ruff lint & mypy |
| `.github/workflows/build.yml` | `workflow_call` / manual | Runs tests, builds standalone binaries (Linux, macOS, Windows via PyInstaller) and the wheel/sdist (`python -m build` + `twine check`) |
| `.github/workflows/release.yml` | push to `main`/`master` | Detects a new version, tags it, builds artifacts, creates a GitHub Release, and publishes to PyPI |

## How a release happens

1. Bump the version in **both** `pyproject.toml` and `claudectl/__init__.py`
   (the release workflow fails if they disagree).
2. Commit and push to `main`.
3. `release.yml` will:
   - see the version has no matching `vX.Y.Z` git tag,
   - create and push that tag,
   - build binaries + wheel/sdist,
   - create a GitHub Release with the binaries and package attached,
   - publish the wheel/sdist to PyPI as `claude-profile`.

If the tag already exists, the release is skipped — so pushing unrelated commits
never re-releases.

## One-time PyPI setup (Trusted Publishing — recommended)

No API token required. On PyPI, signed in as an owner of the **CloudOpsWorld**
organization:

1. Go to **Your organizations → CloudOpsWorld → Publishing → Add a new pending
   publisher** (or **Your account → Publishing** for a personal project) with:
   - PyPI project name: `claude-profile`
   - Owner: `CloudOpsWorld`
   - Repository: `claude-profile`
   - Workflow name: `release.yml`
   - Environment: `pypi`
2. In the GitHub repo, the **Environment** named `pypi` must exist
   (Settings → Environments) — optionally add required reviewers for a manual
   approval gate before publish.

That's it: `release.yml` requests an OIDC token (`id-token: write`) and PyPI
trusts it. The first successful publish creates the project under the org.

### Alternative: API token

If you prefer a token, add a repository secret `PYPI_API_TOKEN` and uncomment the
`password:` line in the `publish-pypi` job of `release.yml`.

## Testing the pipeline first

- Run **Build** manually (Actions → Build → Run workflow) to confirm binaries and
  the package build without cutting a release.
- Optionally point at **TestPyPI** first by adding `repository-url:
  https://test.pypi.org/legacy/` to the publish step.

## Re-running a failed publish

If the publish job failed (for example because the pending publisher wasn't set
up yet), fix the cause and use **Re-run failed jobs** on that workflow run. The
tag and GitHub Release already exist, and `skip-existing: true` makes re-runs safe.

## Local dry run

```bash
pip install build twine
python -m build
twine check dist/*
```
