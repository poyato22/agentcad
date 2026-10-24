import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

// Untrusted text (agent output, tool I/O, Onshape names, paths) must render as text only.
const NO_HTML_INJECTION = [
  { selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']", message: "dangerouslySetInnerHTML is banned: render untrusted text as text." },
  { selector: "MemberExpression[property.name=/^(innerHTML|outerHTML)$/]", message: "innerHTML/outerHTML are banned: use textContent or JSX." },
  { selector: "CallExpression[callee.property.name='insertAdjacentHTML']", message: "insertAdjacentHTML is banned." },
  { selector: "CallExpression[callee.object.name='document'][callee.property.name=/^(write|writeln)$/]", message: "document.write is banned." },
];

export default tseslint.config(
  { ignores: ["dist/**", "node_modules/**", "src-tauri/**", "playwright-report/**", "test-results/**"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx,js}"],
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
    rules: {
      "no-restricted-syntax": ["error", ...NO_HTML_INJECTION],
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_", destructuredArrayIgnorePattern: "^_" }],
    },
  },
  {
    files: ["src/api/**/*.ts"],
    rules: { "@typescript-eslint/no-explicit-any": "error" },
  },
);
