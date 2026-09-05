import js from "@eslint/js";
import globals from "globals";

export default [
  {
    ignores: ["**/dist/**", "**/node_modules/**"],
  },
  {
    files: ["frontend/src/**/*.{js,jsx}"],
    ...js.configs.recommended,
    languageOptions: {
      ecmaVersion: "latest",
      globals: globals.browser,
      parserOptions: {
        ecmaFeatures: { jsx: true },
        sourceType: "module",
      },
    },
  },
];
