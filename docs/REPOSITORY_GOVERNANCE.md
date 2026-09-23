# Repository governance hardening

The codebase can enforce build, test, package and release behavior, but two protections are repository-admin settings rather than source-controlled behavior.

## Main branch

Protect `main` (or create an equivalent repository ruleset) and require pull requests plus the `CI` and `Package Installation Smoke` checks before merge. Disallow force-pushes and branch deletion. Keep administrator bypass limited to emergency recovery.

## Release immutability

Enable GitHub release immutability under repository **Settings > Releases**. The release workflow is ordered as draft -> asset upload -> publish so it is compatible with immutable releases. Once enabled, GitHub prevents edits or deletion of future published release assets/tags through the normal release surface.

These settings should be verified as part of the 1.0 release checklist because they cannot be guaranteed by files committed to the repository.
