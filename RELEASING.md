# CI/CD and releases

## Workflows

| workflow | trigger | what it does |
|---|---|---|
| `ci.yml` (**CI**) | PR or push to `main`/`develop`, manual | ruff, mypy, pytest with coverage on Python 3.11/3.12/3.13; Docker build without push |
| `auto-tag.yml` (**Auto Tag**) | CI succeeded on a push to `main` | works out the next `vX.Y.Z` from Conventional Commits, pushes the tag, then runs Release. **Off until `AUTO_TAG_ENABLED=true`** |
| `release.yml` (**Release**) | `vX.Y.Z` tag pushed, called by Auto Tag, manual with a tag | multi-arch (`linux/amd64`, `linux/arm64`) image to GHCR; GitHub Release with generated notes, `docker-compose.yml` and `.env.example` |

### Images

`ghcr.io/aleksgain/harmony-server` with tags `X.Y.Z`, `X.Y` and `latest`.
After the first release, make the package public: Organisation → Packages →
harmony-server → Package settings → Change visibility.

### Release prerequisites

The Release workflow fails early unless the tagged commit contains `Dockerfile`,
`docker-compose.yml` and `.env.example`.

## Cutting a release

**By hand** (always works):

```sh
git checkout main && git pull
git tag v0.1.0
git push origin v0.1.0
```

**Automatically**: set the repository variable `AUTO_TAG_ENABLED` to `true`
(Settings → Secrets and variables → Actions → Variables). From then on, every
push to `main` whose CI passes is tagged if its commits since the last tag
include a `fix:`, `perf:`, `feat:` or breaking change. `chore:`/`docs:`/`ci:`-only
merges are not released.

Tags pushed by a workflow with `GITHUB_TOKEN` do not start other workflows.
That is why Auto Tag calls Release directly instead of relying on the tag push.
Do not give Auto Tag a PAT: the tag push would then start Release a second time.

To rebuild an existing tag: Actions → Release → Run workflow → enter the tag.

## `.releaserc.json` (semantic-release): not active

`.releaserc.json` holds a [semantic-release](https://semantic-release.gitbook.io/)
configuration. **No workflow runs it.** It is an alternative to Auto Tag, and
running both would create every tag twice. If you switch to semantic-release:

1. Delete `auto-tag.yml`.
2. Add a workflow on push to `main` that runs
   `npx -p semantic-release -p @semantic-release/changelog -p @semantic-release/git semantic-release`.
3. Release will not start from semantic-release's tag push under `GITHUB_TOKEN`.
   Either run Release from that workflow with `workflow_call`, or give
   semantic-release a PAT or GitHub App token.
4. `@semantic-release/git` pushes a `CHANGELOG.md` commit to `main`. That needs
   a token allowed to bypass branch protection.

## Secrets and variables

| name | kind | required | used by | purpose |
|---|---|---|---|---|
| `GITHUB_TOKEN` | built in | yes | all | GHCR push, tags, releases. Nothing to configure |
| `AUTO_TAG_ENABLED` | variable | no | auto-tag | `true` turns on automatic tagging |

Codecov upload uses OIDC (`use_oidc: true`), so it needs no `CODECOV_TOKEN`.
Install the [Codecov GitHub app](https://github.com/apps/codecov) on the
organisation to receive reports. Until then, upload fails and CI still passes.

## Branch protection (recommended)

On `main` and `develop`, require the status checks `Test (Python 3.11)`,
`Test (Python 3.12)`, `Test (Python 3.13)` and `Docker build (no push)`.

## Dependabot

Weekly updates for GitHub Actions, Python (`uv.lock`) and the Dockerfile base
image. Runtime dependency updates use the `fix(deps):` prefix, so with Auto Tag
enabled, merging one cuts a patch release.
