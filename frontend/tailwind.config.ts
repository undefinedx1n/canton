/** @type {import('tailwindcss').Config} */
export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#0f172a",
        muted: "#64748b",
        line: "#e2e8f0",
        brand: {
          50: "#eef6ff",
          100: "#d9ecff",
          200: "#b9d9ff",
          400: "#4f9cf9",
          500: "#2f7ef0",
          600: "#1f63d0",
          700: "#1a4fa8",
        },
        early: "#dbeafe",
        late: "#ffe4e6",
      },
      fontFamily: {
        sans: ["PingFang SC", "Microsoft YaHei", "Noto Sans SC", "system-ui", "sans-serif"],
      },
      boxShadow: {
        card: "0 1px 2px rgba(15,23,42,0.06), 0 8px 24px -12px rgba(15,23,42,0.18)",
      },
    },
  },
  plugins: [],
};
