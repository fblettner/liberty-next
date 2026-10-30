// ESLint baseline (A5, 2026-10-26) — chosen from a measurement, not aspiration: the code
// passes this config with zero errors; the accessibility rules that currently flag ~36
// sites (clickable rows / chips / cards without a keyboard path, autofocus in dialogs) are
// WARNINGS until the keyboard pass (liberty-apps docs/work/PLAN.md, C10), as is react-hooks/exhaustive-deps
// (32 sites, each to be judged — a wrong "fix" there causes real re-render loops).
// `npm run lint` is the command; CI runs it with --max-warnings unlimited but zero errors.
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import jsxA11y from "eslint-plugin-jsx-a11y";
import globals from "globals";

export default tseslint.config(
  { ignores: ["dist/**", "node_modules/**", "eslint.config.js", "vite.config.ts"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    plugins: { "react-hooks": reactHooks, "jsx-a11y": jsxA11y },
    languageOptions: { globals: { ...globals.browser } },
    linterOptions: { reportUnusedDisableDirectives: "error" },
    rules: {
      ...reactHooks.configs.recommended.rules,
      ...jsxA11y.configs.recommended.rules,
      // `cond ? set.delete(x) : set.add(x)` / `ok && fn()` statements are idiomatic here.
      "@typescript-eslint/no-unused-expressions": ["error", { allowShortCircuit: true, allowTernary: true }],
      // `_name` marks an intentionally unused binding (destructuring away a prop, a loop slot).
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_" }],
      // Accessibility backlog — warnings until the keyboard pass lands (PLAN C10).
      "jsx-a11y/click-events-have-key-events": "warn",
      "jsx-a11y/no-static-element-interactions": "warn",
      "jsx-a11y/no-autofocus": "warn",
      "react-hooks/exhaustive-deps": "warn",
      // Every UI string lives in locales/en.ts + fr.ts (C10): a t('key', 'Default') call hides a
      // key the French file never gets. Template-literal defaults (a handful of computed
      // messages) are not matched on purpose.
      "no-restricted-syntax": ["error", {
        selector: "CallExpression[callee.name='t'][arguments.length>=2] > Literal.arguments:nth-child(2)[raw=/^['\"]/]",
        message: "No inline default in t(): add the key to locales/en.ts and fr.ts.",
      }],
    },
  },
);
