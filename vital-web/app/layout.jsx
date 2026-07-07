import "./globals.css";

export const metadata = {
  title: "VITAL — your energy copilot",
  description: "Sleep, energy, activities, ideas, people — one place, agents not search.",
};

// Set the theme BEFORE hydration so there's no light/dark flash.
const themeBoot = `
try {
  document.documentElement.dataset.theme =
    localStorage.getItem("vital_theme") || "dark";
} catch (e) {}
`;

export default function RootLayout({ children }) {
  return (
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBoot }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
