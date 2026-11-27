"use client";

import { useEffect, useState } from "react";
import { MoonIcon, SunIcon } from "./icons";

const KEY = "docchat_theme";

/** Light/dark switch. The choice is remembered in this browser; with no choice it follows the system. */
export default function ThemeToggle({ className = "" }: { className?: string }) {
  const [dark, setDark] = useState(false);
  useEffect(() => setDark(document.documentElement.classList.contains("dark")), []);

  function flip() {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    try {
      window.localStorage.setItem(KEY, next ? "dark" : "light");
    } catch {
      /* storage blocked: the theme still applies for this visit */
    }
  }

  return (
    <button
      type="button"
      onClick={flip}
      aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
      title={dark ? "Light mode" : "Dark mode"}
      className={`rounded-md p-1.5 text-muted hover:bg-sel/60 hover:text-ink ${className}`}
    >
      {dark ? <SunIcon /> : <MoonIcon />}
    </button>
  );
}
