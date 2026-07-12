import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";
import jsxA11y from "eslint-plugin-jsx-a11y";
import betterTailwindcss from "eslint-plugin-better-tailwindcss";

export default tseslint.config(
  {
    ignores: ["dist", "node_modules", "public/mockServiceWorker.js", "src/generated"],
  },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
    },
  },
  // jsx-a11y (Phase 0 wiring) — spread the plugin's flat `recommended`
  // config (registers the plugin + enables 34 rules) then downgrade the
  // single rule that is noisy in a hash/onClick navigation app.
  jsxA11y.flatConfigs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    rules: {
      "jsx-a11y/anchor-is-valid": "warn",
    },
  },
  {
    files: ["**/*.{ts,tsx}"],
    plugins: { "better-tailwindcss": betterTailwindcss },
    settings: {
      "better-tailwindcss": {
        entryPoint: "src/styles/globals.css",
      },
    },
    rules: {
      ...betterTailwindcss.configs.recommended.rules,
      // Prettier owns line wrapping. Enabling both formatters makes each
      // one's auto-fix invalidate the other one's output.
      "better-tailwindcss/enforce-consistent-line-wrapping": "off",
      "better-tailwindcss/no-unknown-classes": [
        "error",
        { ignore: ["^(?:prose-chat|typing-dot|hljs|language-sql)$"] },
      ],
    },
  },
);
