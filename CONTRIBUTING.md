# Contributing to Liberty Next

Thank you for wanting to improve Liberty Next. Contributions come through **forks and pull
requests**: nobody pushes to this repository directly.

## How

1. **Fork** the repository and create a branch from `develop` (the working branch; `main` is
   release-only).
2. Make your change — small and focused: one subject per pull request.
3. Run the checks the CI runs:
   ```bash
   .venv/bin/ruff check liberty tests && .venv/bin/pytest -q
   (cd frontend && npm run lint && npm test && npm run build)
   ```
   New modules come with tests (pytest on the backend, Vitest next to a frontend module). Every
   user-visible text has its key in both `frontend/src/locales/en.ts` and `fr.ts`.
4. Add a line to the open section of `liberty/RELEASE.md` and `liberty/RELEASE.fr.md`.
5. Open a **pull request against `develop`** that says what changes and why.

For anything large, open an issue first so the direction is agreed before you write it.

## The contributor agreement

Liberty Next is published under the Business Source License 1.1 ([LICENSE](LICENSE)), and the
Licensor also distributes it, and products built on it, under other terms. To keep that possible,
every contribution is made under the **[Contributor License Agreement](CLA.md)**: you keep the
copyright of what you wrote, and you give the Licensor the right to use and license it under any
terms.

You accept it by writing in your pull request:

> I have read the Contributor License Agreement (CLA.md) and I agree to it for this contribution
> and my future contributions to Liberty Next.

A pull request without that statement cannot be merged. If you contribute on behalf of your
employer, make sure you are allowed to.

## What not to send

- Secrets, customer data, or code you do not have the right to contribute.
- Code copied from a project whose license does not allow it to be relicensed.
