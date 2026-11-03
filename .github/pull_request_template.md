## Summary

<!-- What does this change, and why? Link any related issue. -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Documentation
- [ ] Tooling or CI

## Checklist

- [ ] `ruff check .`, `ruff format --check .`, `mypy`, and `pytest` pass locally.
- [ ] New behavior is covered by tests that use the fakes in `tests/fake_cloud.py`;
      no test contacts Mopeka or AWS.
- [ ] If entities changed, snapshots were regenerated with `pytest --snapshot-update`
      and the diff reviewed.
- [ ] User-facing changes are described under `## [Unreleased]` in `CHANGELOG.md`.
- [ ] No credentials, tokens, email addresses, or real sensor addresses are included,
      including in fixtures and logs.
