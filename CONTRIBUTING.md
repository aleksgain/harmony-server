# Contributing to harmony-server

Thank you for helping keep Harmony hardware working. This repository is part of
[Harmony Revival](https://github.com/harmony-revival), an open-source replacement
for Logitech's MyHarmony software and cloud.

## The most useful thing you can do right now

**Capture your hub talking to Logitech's cloud while it is still online.**
The config-sync service (`svcs.myharmony.com/Discovery/Discovery.svc`) is not
documented anywhere, and once Logitech turns it off, the traffic can never be
recorded again. Follow the
[hub capture template](https://github.com/harmony-revival/harmony-server/issues/new?template=hub-capture.md).
Read its redaction section before posting anything: captures contain account
tokens.

## Ground rules

- Be decent. See the [Code of Conduct](https://github.com/harmony-revival/.github/blob/main/.github/CODE_OF_CONDUCT.md).
- Security problems go to the [security policy](https://github.com/harmony-revival/.github/blob/main/.github/SECURITY.md), not to public issues.
- Do not contribute Logitech code, binaries, private keys or certificates, or
  anything copied from decompiled Logitech software. Describe protocols in your
  own words and with your own captures.
- By contributing, you agree that your contribution is licensed under the
  [Apache License 2.0](LICENSE).

## Workflow

1. Open an issue first for anything bigger than a small fix.
2. Fork, then branch from `develop`. `main` holds released code.
3. Open the PR against `develop`. CI must pass.
4. Maintainers merge `develop` into `main` to release.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/). Release
versions are calculated from them:

| prefix | effect on the next release |
|---|---|
| `fix:`, `perf:` | patch (`0.1.0` → `0.1.1`) |
| `feat:` | minor (`0.1.0` → `0.2.0`) |
| `feat!:` or a `BREAKING CHANGE:` footer | major |
| `chore:`, `docs:`, `ci:`, `test:`, `refactor:`, `style:`, `build:` | none on its own |

Example: `fix(provisioning): accept firmware 4.15.600 discovery response`.

If PRs are squash-merged, the PR title becomes the commit message, so it must
follow the same format.

## Development

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --all-extras
uv run ruff check src/
uv run mypy src/harmony_server
uv run pytest tests/ --cov=harmony_server
docker build -t harmony-server:dev .
```

These are the same checks CI runs (`.github/workflows/ci.yml`), on Python 3.11,
3.12 and 3.13. See [RELEASING.md](RELEASING.md) for how releases work.

