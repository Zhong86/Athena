import type { Metadata } from "next";
import { Fraunces, Inter } from "next/font/google";
import "./globals.css";

import { GetStartedGuide } from "@/components/GetStartedGuide";
import { PageTransition } from "@/components/PageTransition";
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
  description: "A study agent that acts as a guide from your notes",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${fraunces.variable} ${inter.variable}`}>
        <RoadmapCreationProvider>
          <RoadmapCreationBanner />
          <PageTransition>{children}</PageTransition>
          {/* Persistent across navigations so its onboarding-progress fetch
              doesn't remount (and race the page's view transition) every
              time the route changes. */}
          <GetStartedGuide />
        </RoadmapCreationProvider>
      </body>
    </html>
  );
}
