import { useCallback, useEffect, useState } from "react";

type Theme = "light" | "dark" | "system";
const KEY = "novara.theme";

/** data-theme on <html> overrides prefers-color-scheme (tokens.css handles both). */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      const saved = localStorage.getItem(KEY);
      return saved === "light" || saved === "dark" ? saved : "system";
    } catch {
      return "system";
    }
  });
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
  }, [theme]);
  const toggle = useCallback(() => {
    const prefersDark = window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
    const current = theme === "system" ? (prefersDark ? "dark" : "light") : theme;
    const next = current === "dark" ? "light" : "dark";
    setTheme(next);
    try {
      localStorage.setItem(KEY, next);
    } catch {
      /* ignore */
    }
  }, [theme]);
  return { theme, toggle };
}
