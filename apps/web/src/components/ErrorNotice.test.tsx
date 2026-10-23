import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import { I18nProvider } from "../i18n/I18n";
import { ErrorNotice, Notice } from "./ui";

const show = (error: unknown) => render(<I18nProvider lang="en"><ErrorNotice error={error} /></I18nProvider>);

describe("ErrorNotice", () => {
  it("shows a message written for the reader as is", () => {
    show(new Notice("Wrong password or code."));
    expect(screen.getByRole("alert").textContent).toBe("Wrong password or code.");
  });

  it("shows the server's message", () => {
    show(new ApiError(409, "the books are closed up to 2026-03-31"));
    expect(screen.getByRole("alert").textContent).toContain("the books are closed");
  });

  it("keeps unexpected errors generic", () => {
    show(new TypeError("Failed to fetch"));
    expect(screen.getByRole("alert").textContent).not.toContain("Failed to fetch");
  });
});
