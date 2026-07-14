// theme-config.js — shared Tailwind design tokens for all VerdictAI pages.
// Loaded right after the Tailwind Play CDN script.
tailwind.config = {
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Brand: deep indigo/navy — authority & trust.
        brand: {
          50: "#eef2ff", 100: "#e0e7ff", 200: "#c7d2fe", 300: "#a5b4fc",
          400: "#818cf8", 500: "#6366f1", 600: "#4f46e5", 700: "#4338ca",
          800: "#3730a3", 900: "#312e81", 950: "#1e1b4b",
        },
        // Accent: amber/gold — the scales of justice.
        accent: {
          50: "#fffbeb", 100: "#fef3c7", 200: "#fde68a", 300: "#fcd34d",
          400: "#fbbf24", 500: "#f59e0b", 600: "#d97706", 700: "#b45309",
          800: "#92400e", 900: "#78350f",
        },
      },
      fontFamily: {
        sans: ["Public Sans", "Noto Sans Devanagari", "system-ui", "sans-serif"],
        display: ["Lora", "Georgia", "serif"],
        deva: ["Noto Sans Devanagari", "Public Sans", "sans-serif"],
      },
      maxWidth: { "4.5xl": "60rem" },
    },
  },
};
