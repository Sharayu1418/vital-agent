import "./globals.css";

export const metadata = {
  title: "VITAL — your energy copilot",
  description: "Sleep, energy, activities, ideas, people — one place, agents not search.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
