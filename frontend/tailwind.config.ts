import type { Config } from "tailwindcss";

// Colours are CSS variables (see app/globals.css) so a dark theme is a one-block change later.
const c = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

const config: Config = {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.ts"],
  theme: {
    extend: {
      colors: {
        bg: c("bg"),
        panel: c("panel"),
        side: c("side"), // sidebar tint
        sel: c("sel"), // selected item
        ink: c("ink"),
        muted: c("muted"),
        line: c("line"),
        done: c("done"), // finished / trusted
        live: c("live"), // work in progress (saffron)
        bad: c("bad"),
      },
      fontFamily: {
        sans: ['"Hanken Grotesk Variable"', "Nirmala UI", "Noto Sans Devanagari", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};

export default config;
