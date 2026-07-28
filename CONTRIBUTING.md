# Contributing

Thanks for helping improve netbox-opennms-plugin! The full developer guide —
code layout, local NetBox + OpenNMS stack, end-to-end verification — lives at
**<https://no42-org.github.io/netbox-opennms-plugin/>**. This file covers the
rules every contribution must follow.

## Workflow

1. Open (or find) a GitHub issue describing the bug or enhancement — work
   starts from an issue, not a drive-by PR.
2. Branch from `main`, make your change, and run `make verify` (ruff + the full
   unit suite in a throwaway NetBox stack). `make help` lists all targets.
3. Open a PR referencing the issue with a closing keyword (`Closes #123`).
   PRs are squash-merged once CI is green.

## Commit conventions

- **Conventional Commits**: `<type>(scope): description` with type one of
  `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`, `build`, `perf`,
  `style`, `revert`. Breaking changes append `!` or add a `BREAKING CHANGE:`
  footer.
- **SPDX header** on every new or edited source file
  (`SPDX-License-Identifier: MIT`).

## Developer Certificate of Origin

All commits must be signed off (`git commit -s`), certifying the
[DCO](https://developercertificate.org/). The `Signed-off-by` trailer must name
a human identity — the person responsible for the contribution.

## AI-assisted contributions

AI assistance is welcome. Commits produced with an AI agent additionally carry
an `Assisted-by: <Agent>:<model>` trailer (e.g.
`Assisted-by: ClaudeCode:claude-fable-5`). The human signer reviews all
AI-generated code and remains responsible for its correctness and license
compliance.

## Releases

See [RELEASING.md](./RELEASING.md).
