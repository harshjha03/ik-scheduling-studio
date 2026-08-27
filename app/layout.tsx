import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "IK Scheduling studio",
  description: "Interview Kickstart — SME-to-session scheduling agent",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
