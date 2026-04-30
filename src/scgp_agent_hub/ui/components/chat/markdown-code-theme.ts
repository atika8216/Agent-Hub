import type { CSSProperties } from "react";

/*
 * Clarity syntax highlight theme for ``react-syntax-highlighter`` /
 * Prism. The prior theme (``oneDark``) shipped violet keywords and a
 * near-black body that read as a foreign object inside our warm-neutral
 * card palette. This theme pulls from the same design tokens that drive
 * the rest of the UI so a code block feels like a first-class child of
 * its bubble:
 *
 *   - body text: ``--color-text-primary`` (honors light/dark toggle)
 *   - keywords / selectors: ``--color-info`` (iOS system blue)
 *   - strings / doctypes: soft green that reads on both themes
 *   - numbers / constants: SCGP red-orange family for brand moments
 *   - comments: ``--color-text-muted`` in italics
 *
 * We intentionally don't set a background on ``pre`` / ``code`` tokens;
 * the wrapping ``<div>`` already owns the ``bg-surface-elevated`` card
 * styling, so a secondary background here would fight the card shell.
 */

const commonCode: CSSProperties = {
  color: "var(--color-text-primary)",
  background: "transparent",
  textShadow: "none",
  fontFamily:
    "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace",
  fontSize: "0.8125rem",
  lineHeight: 1.5,
  tabSize: 4,
};

export const clarityCodeTheme: { [key: string]: CSSProperties } = {
  'code[class*="language-"]': commonCode,
  'pre[class*="language-"]': {
    ...commonCode,
    padding: 0,
    margin: 0,
    overflow: "auto",
  },
  comment: {
    color: "var(--color-text-muted)",
    fontStyle: "italic",
  },
  prolog: { color: "var(--color-text-muted)" },
  doctype: { color: "var(--color-text-muted)" },
  cdata: { color: "var(--color-text-muted)" },
  punctuation: { color: "var(--color-text-secondary)" },
  namespace: { opacity: 0.7 },
  property: { color: "var(--color-info)" },
  tag: { color: "var(--color-info)" },
  "attr-name": { color: "var(--color-info)" },
  boolean: { color: "var(--color-primary)" },
  number: { color: "var(--color-primary)" },
  constant: { color: "var(--color-primary)" },
  symbol: { color: "var(--color-primary)" },
  deleted: { color: "var(--color-error, oklch(0.55 0.16 27))" },
  selector: { color: "var(--color-info)" },
  "attr-value": { color: "var(--color-success, oklch(0.55 0.12 150))" },
  string: { color: "var(--color-success, oklch(0.55 0.12 150))" },
  char: { color: "var(--color-success, oklch(0.55 0.12 150))" },
  builtin: { color: "var(--color-info)" },
  inserted: { color: "var(--color-success, oklch(0.55 0.12 150))" },
  operator: { color: "var(--color-text-secondary)" },
  entity: { color: "var(--color-info)", cursor: "help" },
  url: { color: "var(--color-info)" },
  "language-css .token.string": {
    color: "var(--color-success, oklch(0.55 0.12 150))",
  },
  ".style .token.string": {
    color: "var(--color-success, oklch(0.55 0.12 150))",
  },
  atrule: { color: "var(--color-info)" },
  keyword: { color: "var(--color-info)", fontWeight: 500 },
  function: { color: "var(--color-info)" },
  regex: { color: "var(--color-primary)" },
  important: { color: "var(--color-primary)", fontWeight: 600 },
  variable: { color: "var(--color-text-primary)" },
  bold: { fontWeight: 600 },
  italic: { fontStyle: "italic" },
};
