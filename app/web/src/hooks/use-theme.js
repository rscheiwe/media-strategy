import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "chat:theme";

function systemTheme() {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function storedTheme() {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value === "dark" || value === "light" ? value : null;
  } catch {
    return null;
  }
}

/**
 * Light/dark theme: the choice
 * is kept in localStorage, defaults to the OS preference, and is applied as the
 * `.dark` class on <html> (Tailwind `darkMode: ["class"]`).
 */
export function useTheme() {
  const [resolvedTheme, setResolvedTheme] = useState(() => storedTheme() ?? systemTheme());

  useEffect(() => {
    document.documentElement.classList.toggle("dark", resolvedTheme === "dark");
  }, [resolvedTheme]);

  const toggleTheme = useCallback(() => {
    setResolvedTheme((current) => {
      const next = current === "dark" ? "light" : "dark";
      try {
        window.localStorage.setItem(STORAGE_KEY, next);
      } catch {
        // storage unavailable (private mode): keep the choice for this session only
      }
      return next;
    });
  }, []);

  return { resolvedTheme, toggleTheme };
}
