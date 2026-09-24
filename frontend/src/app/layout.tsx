import type { Metadata } from "next";
import { Fraunces, Inter } from "next/font/google";
import "./globals.css";

import { RoadmapCreationBanner } from "@/components/RoadmapCreationBanner";
import { RoadmapCreationProvider } from "@/lib/roadmapCreation";

const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});

export const metadata: Metadata = {
  title: "Αθηνα",
  description: "A study agent that routes Materials and Goal signals.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${fraunces.variable} ${inter.variable}`}>
        <RoadmapCreationProvider>
          <RoadmapCreationBanner />
          {children}
        </RoadmapCreationProvider>
      </body>
    </html>
  );
}
