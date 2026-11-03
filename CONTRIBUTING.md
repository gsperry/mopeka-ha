# Contributing

Thanks for helping improve Mopeka HA. Bug reports, fixes, and documentation improvements
are welcome. For larger changes, please open an issue first so we can agree on the
approach.

## Development setup

You need [uv](https://docs.astral.sh/uv/) and Git. uv installs the right Python version.

```bash
git clone https://github.com/gsperry/mopeka-ha.git
cd mopeka-ha
uv venv --python 3.14
source .venv/bin/activate
uv pip install -r requirements_test.txt
pre-commit install
```

`requirements_test.txt` pins every tool, and through
`pytest-homeassistant-custom-component`, the Home Assistant version the tests run against.

## Checks

CI runs these on every push and pull request. Run them before you push:

```bash
ruff check .
ruff format --check .
mypy
pytest --cov
```

The coverage floor is 90 %. CI also runs Home Assistant's hassfest and the HACS
validation.

## How the code is organized

| Path | Contents |
|---|---|
| `custom_components/mopeka_ha/api.py` | Cloud client: Cognito sign-in, token refresh, platform detection, and both data APIs. |
| `custom_components/mopeka_ha/models.py` | Parsing of API responses and the level, fill, and volume calculations. |
| `custom_components/mopeka_ha/coordinator.py` | The `DataUpdateCoordinator`. |
| `custom_components/mopeka_ha/sensor.py` | Entity descriptions and the sensor entity. |
| `custom_components/mopeka_ha/config_flow.py` | Setup, re-authentication, reconfigure, and options. |
| `custom_components/mopeka_ha/__init__.py` | Setup, unload, and migration from earlier releases. |
| `tests/fake_cloud.py` | In-memory fakes of Amazon Cognito and both Mopeka platforms. |

## Tests

Tests must never contact Mopeka or AWS. The test plugin blocks network access, and
`tests/fake_cloud.py` provides fakes instead:

- `FakeCognito` implements the server side of Cognito's SRP sign-in, so sign-in only
  succeeds if the client computes a correct password proof. The current platform's pool
  hides whether an account exists, like the real one.
- `FakeRetail` serves the current platform's sync route and fails any request whose body
  is not the empty sync. A non-empty body would change the user's account.
- `FakeGateway` serves the older platform's gateway.

Each fake can queue errors, expire tokens, and record requests, so tests can cover
failures as well as the happy path.

### Snapshots

`tests/test_fixtures.py` and `tests/test_diagnostics.py` compare entities and
diagnostics with snapshots in `tests/snapshots/`. If you change entities or diagnostics
on purpose, regenerate them and review the diff before committing:

```bash
pytest --snapshot-update
git diff tests/snapshots/
```

### Fixtures

`tests/fixtures/` holds API responses. `tests/fixtures/README.md` records where each one
came from. To record new ones from your own account, see the next section, then check the
files by hand before committing them.

## Testing against a real account

`scripts/live_check.py` signs in with the integration's own client and prints what Mopeka
Cloud returns, using placeholder names. Put your credentials in `.env`, which Git
ignores:

```bash
cp .env.example .env    # then edit .env
uv run scripts/live_check.py
uv run scripts/live_check.py --record tests/fixtures
```

Never commit credentials, tokens, email addresses, or real sensor addresses.

## Pull requests

- Keep each pull request to one change, with tests.
- Describe user-facing changes under `## [Unreleased]` in `CHANGELOG.md`.
- Write commit messages in the imperative mood ("Add volume sensor"), with a body that
  explains why when it is not obvious.

## Releases

Maintainers release from GitHub:

1. Make sure `## [Unreleased]` in `CHANGELOG.md` describes the release.
2. Run the **Release** workflow from the Actions tab and enter the version, such as
   `2.1.0`, or `2.1.0b1` for a beta.

The workflow runs all checks, sets the version in `manifest.json` and
`pyproject.toml`, dates the changelog, commits, tags `v<version>`, and publishes a GitHub
release with the `mopeka_ha.zip` that HACS installs. Versions with `a`, `b`, or `rc` are
published as pre-releases.

## Code of conduct

Be respectful and constructive. Assume good intent, and keep discussion focused on the
work.
