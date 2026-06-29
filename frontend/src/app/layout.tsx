import type { Metadata } from "next";
import type { ReactNode } from "react";

import { QueryProvider } from "@/lib/query-provider";

import "./globals.css";

export const metadata: Metadata = {
  title: "Basketball Data Emporium",
  description: "Basketball Reference data workbench for player and team research.",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <QueryProvider>{children}</QueryProvider>
      </body>
    </html>
  );
}
