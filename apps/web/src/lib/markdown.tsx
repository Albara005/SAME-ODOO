/**
 * A small Markdown subset for the assistant's replies, rendered as React elements (never as HTML),
 * so nothing in a reply can inject markup or script. Supported: paragraphs and line breaks,
 * headings (#, ##, ###), **bold**, *italic*, `code`, fenced code blocks, bullet and numbered
 * lists, tables, and [links](https://...) (http and https only). Anything else stays plain text.
 * Half-written Markdown (while a reply streams in) renders as plain text until it closes.
 */

import { Fragment, type ReactNode } from "react";

export type Block =
  | { kind: "heading"; level: 1 | 2 | 3; text: string }
  | { kind: "paragraph"; lines: string[] }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "code"; text: string }
  | { kind: "table"; header: string[]; rows: string[][] };

const BULLET = /^\s*[-*•]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;
const HEADING = /^(#{1,3})\s+(.*)$/;
const TABLE_RULE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

const cells = (line: string) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());

export function parseBlocks(text: string): Block[] {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    if (!line.trim()) {
      i++;
      continue;
    }
    if (line.trim().startsWith("```")) {
      const end = lines.findIndex((l, j) => j > i && l.trim().startsWith("```"));
      if (end !== -1) {
        blocks.push({ kind: "code", text: lines.slice(i + 1, end).join("\n") });
        i = end + 1;
        continue;
      }
    }
    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({ kind: "heading", level: heading[1]!.length as 1 | 2 | 3, text: heading[2]! });
      i++;
      continue;
    }
    if (line.includes("|") && i + 1 < lines.length && TABLE_RULE.test(lines[i + 1]!)) {
      const header = cells(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i]!.includes("|") && lines[i]!.trim()) {
        rows.push(cells(lines[i]!));
        i++;
      }
      blocks.push({ kind: "table", header, rows });
      continue;
    }
    const ordered = !BULLET.test(line) && NUMBERED.test(line);
    if (ordered || BULLET.test(line)) {
      const pattern = ordered ? NUMBERED : BULLET;
      const items: string[] = [];
      while (i < lines.length && pattern.test(lines[i]!)) {
        items.push(pattern.exec(lines[i]!)![1]!);
        i++;
      }
      blocks.push({ kind: "list", ordered, items });
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i]!.trim() && !HEADING.test(lines[i]!) && !BULLET.test(lines[i]!)
           && !NUMBERED.test(lines[i]!) && !lines[i]!.trim().startsWith("```")) {
      para.push(lines[i]!);
      i++;
    }
    if (para.length === 0) {  // an unclosed ``` fence: keep it as text
      para.push(line);
      i++;
    }
    blocks.push({ kind: "paragraph", lines: para });
  }
  return blocks;
}

const INLINE = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\s][^*\n]*\*)|(\[[^\]\n]+\]\([^)\s]+\))/;

/** Inline formatting: code first (its contents stay literal), then bold, italic and links. */
export function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let rest = text;
  let key = 0;
  while (rest) {
    const m = INLINE.exec(rest);
    if (!m) {
      out.push(rest);
      break;
    }
    if (m.index > 0) out.push(rest.slice(0, m.index));
    const token = m[0];
    if (m[1]) out.push(<code key={key++}>{token.slice(1, -1)}</code>);
    else if (m[2]) out.push(<strong key={key++}>{inline(token.slice(2, -2))}</strong>);
    else if (m[3]) out.push(<em key={key++}>{inline(token.slice(1, -1))}</em>);
    else {
      const [, label = "", href = ""] = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token) ?? [];
      out.push(/^https?:\/\//i.test(href)
        ? <a key={key++} href={href} target="_blank" rel="noopener noreferrer nofollow">{label}</a>
        : token);
    }
    rest = rest.slice(m.index + token.length);
  }
  return out;
}

export function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      {parseBlocks(text).map((b, i) => {
        if (b.kind === "heading") return <p key={i} className={`md-h md-h${b.level}`}><strong>{inline(b.text)}</strong></p>;
        if (b.kind === "code") return <pre key={i} dir="ltr"><code>{b.text}</code></pre>;
        if (b.kind === "list") {
          const items = b.items.map((it, j) => <li key={j}>{inline(it)}</li>);
          return b.ordered ? <ol key={i}>{items}</ol> : <ul key={i}>{items}</ul>;
        }
        if (b.kind === "table")
          return (
            <div key={i} className="md-table">
              <table>
                <thead><tr>{b.header.map((h, j) => <th key={j}>{inline(h)}</th>)}</tr></thead>
                <tbody>{b.rows.map((r, j) => <tr key={j}>{b.header.map((_, k) => <td key={k}>{inline(r[k] ?? "")}</td>)}</tr>)}</tbody>
              </table>
            </div>
          );
        return (
          <p key={i}>
            {b.lines.map((l, j) => <Fragment key={j}>{j > 0 && <br />}{inline(l)}</Fragment>)}
          </p>
        );
      })}
    </div>
  );
}
