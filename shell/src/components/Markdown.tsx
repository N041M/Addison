// Markdown rendering for assistant messages (never user input — see ChatThread).
//
// Safety is the whole point of this file:
//   - NO rehype-raw. `skipHtml` drops any raw HTML in the model's output, so a
//     message can never inject markup into the webview.
//   - react-markdown's default urlTransform stays on — it strips `javascript:`
//     and other dangerous URL schemes before a link ever reaches our renderer.
//   - Links do NOT navigate. The webview must never open URLs itself, and must
//     never call any shell.* IPC method. We render a styled, inert anchor and
//     surface the destination via the hover `title` only.
//   - Fenced `mermaid` blocks render as display-only diagrams (mermaid's own
//     sanitized SVG under securityLevel: "strict"); every other language is
//     syntax-highlighted by rehype-highlight.

import { memo, type ComponentPropsWithoutRef } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { MermaidDiagram } from "./MermaidDiagram";

interface Props {
  content: string;
  /** The containing assistant message is still streaming. */
  pending?: boolean;
}

// The fence language className react-markdown/rehype leave on a code element,
// e.g. `language-mermaid` or `language-python hljs`.
function fenceLanguage(className: string | undefined): string | null {
  const match = /language-([\w-]+)/.exec(className ?? "");
  return match ? match[1] : null;
}

// Build the components map once per `pending` value — the mermaid branch needs
// to know whether the message is still streaming (a half-written diagram must
// not attempt to render).
function buildComponents(pending: boolean): Components {
  return {
    // Inert, display-only link. It never navigates and never touches shell.*
    // IPC — the destination is shown on hover only.
    // TODO(shell capability): real link-opening goes through the Rust shell, never the webview
    a({ href, children }: ComponentPropsWithoutRef<"a"> & { href?: string }) {
      return (
        <a
          title={href}
          role="link"
          onClick={(e) => e.preventDefault()}
          className="text-fern-deep underline"
        >
          {children}
        </a>
      );
    },

    code({ className, children, ...rest }: ComponentPropsWithoutRef<"code">) {
      const language = fenceLanguage(className);
      // A `mermaid` fence in a settled message becomes a rendered diagram; while
      // the message is still streaming the code is almost certainly incomplete,
      // so we leave it as plain code until the turn lands.
      if (language === "mermaid" && !pending) {
        return <MermaidDiagram code={String(children).replace(/\n$/, "")} />;
      }
      // Everything else: default rendering. Inline code and non-mermaid fenced
      // blocks (already highlighted by rehype-highlight) pass straight through.
      return (
        <code className={className} {...rest}>
          {children}
        </code>
      );
    },
  };
}

function MarkdownImpl({ content, pending = false }: Props) {
  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { detect: false }]]}
        skipHtml
        components={buildComponents(pending)}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

export const Markdown = memo(MarkdownImpl);
