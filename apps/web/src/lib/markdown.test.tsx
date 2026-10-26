import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown, parseBlocks } from "./markdown";

describe("parseBlocks", () => {
  it("finds paragraphs, headings, lists, code and tables", () => {
    const text = [
      "## Overdue invoices", "", "Three invoices are late:", "- **INV-1** 1,150", "- INV-2", "",
      "1. Call them", "2. Send a reminder", "", "| Customer | Due |", "|---|---:|", "| Hala | 1,150 |", "",
      "```", "<b>not bold</b>", "```", "Last line", "second line",
    ].join("\n");
    expect(parseBlocks(text)).toEqual([
      { kind: "heading", level: 2, text: "Overdue invoices" },
      { kind: "paragraph", lines: ["Three invoices are late:"] },
      { kind: "list", ordered: false, items: ["**INV-1** 1,150", "INV-2"] },
      { kind: "list", ordered: true, items: ["Call them", "Send a reminder"] },
      { kind: "table", header: ["Customer", "Due"], rows: [["Hala", "1,150"]] },
      { kind: "code", text: "<b>not bold</b>" },
      { kind: "paragraph", lines: ["Last line", "second line"] },
    ]);
  });

  it("keeps an unclosed code fence as text (while a reply streams in)", () => {
    expect(parseBlocks("```\nconst a = 1")).toEqual([
      { kind: "paragraph", lines: ["```"] }, { kind: "paragraph", lines: ["const a = 1"] },
    ]);
  });
});

describe("Markdown", () => {
  it("never renders HTML from the text", () => {
    const { container } = render(<Markdown text={'<img src=x onerror="alert(1)"> **hi** <script>alert(2)</script>'} />);
    expect(container.querySelector("img, script")).toBeNull();
    expect(container.querySelector("strong")?.textContent).toBe("hi");
    expect(container.textContent).toContain("<script>alert(2)</script>");
  });

  it("links only to http and https", () => {
    const { container } = render(<Markdown text="[docs](https://example.com) and [bad](javascript:alert(1))" />);
    const links = container.querySelectorAll("a");
    expect(links).toHaveLength(1);
    expect(links[0]!.getAttribute("href")).toBe("https://example.com");
    expect(links[0]!.getAttribute("rel")).toContain("noopener");
    expect(container.textContent).toContain("[bad](javascript:alert(1))");
  });

  it("formats inline code, bold and italics", () => {
    const { container } = render(<Markdown text="Use `**raw**` then **bold** and *soft*" />);
    expect(container.querySelector("code")?.textContent).toBe("**raw**");
    expect(container.querySelector("strong")?.textContent).toBe("bold");
    expect(container.querySelector("em")?.textContent).toBe("soft");
  });
});
