import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CatalogAgent — Bounded Autonomous Negotiation",
  description:
    "B2B procurement negotiation where an LLM proposes and a deterministic guardrail disposes.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
