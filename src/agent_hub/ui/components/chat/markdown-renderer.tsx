import { memo, useCallback, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { Check, Copy } from "lucide-react";

import { clarityCodeTheme } from "./markdown-code-theme";

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    // 1.2 s is long enough that the user sees the check glyph but short
    // enough that the button snaps back before they reach for another
    // code block. Matches the iOS "Copied" toast duration.
    setTimeout(() => setCopied(false), 1200);
  }, [text]);

  return (
    <button
      type="button"
      onClick={handleCopy}
      aria-label={copied ? "Copied" : "Copy code"}
      className={[
        "absolute right-2 top-2 z-10 inline-flex items-center gap-1",
        "rounded-md border border-border/60 bg-surface-elevated/80 px-1.5 py-1",
        "text-[0.6875rem] font-medium text-text-secondary",
        "backdrop-blur transition-all",
        "hover:bg-surface-overlay hover:text-text-primary",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-info/40",
      ].join(" ")}
      title={copied ? "Copied" : "Copy code"}
    >
      {copied ? (
        <>
          <Check className="h-3 w-3 text-success" />
          <span className="hidden sm:inline">Copied</span>
        </>
      ) : (
        <>
          <Copy className="h-3 w-3" />
          <span className="hidden sm:inline">Copy</span>
        </>
      )}
    </button>
  );
}

/*
 * Document-rhythm markdown.
 *
 * We deliberately DON'T use ``prose`` from the Tailwind Typography
 * plugin here: it bakes in its own heading scale and color palette
 * that fight our Clarity tokens. Instead, the wrapper div sets a
 * comfortable base (15px body, 1.55 leading) and every block element
 * that matters gets an explicit ``components`` override so headings,
 * paragraphs, and lists land on a predictable vertical rhythm inside
 * the assistant hairline card.
 *
 * Headings use the display face (--font-display); body, lists, and
 * inline text stay in the text face. ``last:mb-0`` on the bottom
 * element of each group keeps the trailing gap from pushing against
 * the hover copy bar below.
 */
export const MarkdownRenderer = memo(function MarkdownRenderer({
  content,
}: {
  content: string;
}) {
  return (
    <div className="max-w-none text-[0.9375rem] leading-[1.55] text-text-primary">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1({ children }) {
            return (
              <h1
                className={[
                  "mt-6 mb-3 first:mt-0",
                  "text-[1.5rem] font-bold leading-[1.2]",
                  "tracking-[-0.01em] text-text-primary",
                  "font-[family-name:var(--font-display)]",
                ].join(" ")}
              >
                {children}
              </h1>
            );
          },
          h2({ children }) {
            return (
              <h2
                className={[
                  "mt-5 mb-2 first:mt-0",
                  "text-[1.1875rem] font-semibold leading-[1.3]",
                  "tracking-[-0.005em] text-text-primary",
                  "font-[family-name:var(--font-display)]",
                ].join(" ")}
              >
                {children}
              </h2>
            );
          },
          h3({ children }) {
            return (
              <h3
                className={[
                  "mt-4 mb-1.5 first:mt-0",
                  "text-[1.0625rem] font-semibold leading-[1.35]",
                  "text-text-primary",
                ].join(" ")}
              >
                {children}
              </h3>
            );
          },
          h4({ children }) {
            return (
              <h4 className="mt-3 mb-1 first:mt-0 text-[0.9375rem] font-semibold text-text-primary">
                {children}
              </h4>
            );
          },
          p({ children }) {
            return (
              <p className="mb-3 last:mb-0 leading-[1.55] text-text-primary">
                {children}
              </p>
            );
          },
          ul({ children }) {
            return (
              <ul className="my-3 space-y-1.5 pl-5 list-disc marker:text-text-muted">
                {children}
              </ul>
            );
          },
          ol({ children }) {
            return (
              <ol className="my-3 space-y-1.5 pl-5 list-decimal marker:text-text-muted">
                {children}
              </ol>
            );
          },
          li({ children }) {
            return <li className="leading-[1.5] text-text-primary">{children}</li>;
          },
          strong({ children }) {
            return (
              <strong className="font-semibold text-text-primary">{children}</strong>
            );
          },
          em({ children }) {
            return <em className="italic">{children}</em>;
          },
          a({ href, children }) {
            return (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="text-info underline-offset-[3px] hover:underline"
              >
                {children}
              </a>
            );
          },
          blockquote({ children }) {
            return (
              <blockquote className="my-3 border-l-2 border-border pl-4 text-text-secondary italic">
                {children}
              </blockquote>
            );
          },
          hr() {
            return <hr className="my-5 border-0 border-t border-border" />;
          },
          code({ className, children, ...props }) {
            const match = /language-(\w+)/.exec(className ?? "");
            const codeStr = String(children).replace(/\n$/, "");

            if (match) {
              return (
                <div className="group relative my-3 overflow-hidden rounded-[var(--radius-sm)] border border-border bg-surface-elevated">
                  <CopyButton text={codeStr} />
                  <SyntaxHighlighter
                    style={clarityCodeTheme}
                    language={match[1]}
                    PreTag="div"
                    customStyle={{
                      margin: 0,
                      padding: "0.75rem 1rem",
                      borderRadius: 0,
                      background: "transparent",
                      fontSize: "0.8125rem",
                      lineHeight: 1.5,
                    }}
                  >
                    {codeStr}
                  </SyntaxHighlighter>
                </div>
              );
            }

            return (
              <code
                className="rounded bg-surface-elevated px-1.5 py-0.5 text-[0.8125rem] font-mono text-info"
                {...props}
              >
                {children}
              </code>
            );
          },
          table({ children }) {
            return (
              <div className="my-3 overflow-x-auto">
                <table className="min-w-full border-collapse border border-border text-[0.875rem]">
                  {children}
                </table>
              </div>
            );
          },
          th({ children }) {
            return (
              <th className="border border-border bg-surface-elevated px-3 py-1.5 text-left font-medium">
                {children}
              </th>
            );
          },
          td({ children }) {
            return (
              <td className="border border-border px-3 py-1.5">{children}</td>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});
