import Image from "next/image";
import Link from "next/link";

import { Nav } from "@/components/Nav";

import styles from "./landing.module.css";

export const metadata = {
  title: "Αθηνα — a study agent that reads your own material",
  description:
    "Upload your material, set a goal, and let Αθηνα track what you actually understand.",
};

/**
 * The explainer at "/". Everything described here is wired up today — the
 * dashboard, goals, materials, sessions, quizzes and knowledge-sync routes.
 * Settings is deliberately absent: that page is still a placeholder.
 */

type Feature = {
  href: string;
  glyph: string;
  title: string;
  body: string;
  accent: string;
  accentSoft: string;
};

const FEATURES: Feature[] = [
  {
    href: "/goal",
    glyph: "◎",
    title: "Goals",
    body: "Say what you want to learn and Αθηνα drafts a roadmap for it — milestones in the order they should be taken, each tied to a topic. Every goal carries a percent done and a current focus.",
    accent: "var(--indigo)",
    accentSoft: "var(--indigo-soft)",
  },
  {
    href: "/materials",
    glyph: "▤",
    title: "Materials",
    body: "Upload slides, notes and readings, or paste text straight in. Each file is split into chunks, tagged into topics, and given an understanding score so you can see where you actually stand.",
    accent: "var(--coral)",
    accentSoft: "var(--coral-soft)",
  },
  {
    href: "/sessions",
    glyph: "◍",
    title: "Sessions",
    body: "Chat with Αθηνα about anything in your material. It answers from your own chunks rather than from thin air, and what it notices in the conversation feeds your topic scores.",
    accent: "var(--sage)",
    accentSoft: "var(--sage-soft)",
  },
  {
    href: "/quizzes",
    glyph: "✓",
    title: "Quizzes",
    body: "Quizzes are generated from a topic you have material for. Answers are graded against the source chunks, and the result is recorded as evidence behind that topic's score.",
    accent: "var(--amber-ink)",
    accentSoft: "var(--amber-soft)",
  },
  {
    href: "/knowledge-sync",
    glyph: "↻",
    title: "Knowledge-Sync",
    body: "The log of what Αθηνα did without being asked — scheduled runs across your material and goals, and the actions it took off the back of them.",
    accent: "var(--indigo-deep)",
    accentSoft: "var(--indigo-wash)",
  },
  {
    href: "/dashboard",
    glyph: "◆",
    title: "Dashboard",
    body: "The one screen for today: goals still in progress, the last check-in Αθηνα made, and the weak topics that are shaping what it thinks you should work on.",
    accent: "var(--coral)",
    accentSoft: "var(--coral-soft)",
  },
];

const LOOP = [
  {
    title: "Material goes in",
    body: "Files and pasted text are chunked and sorted into topics automatically.",
  },
  {
    title: "Understanding is scored",
    body: "Quiz results and what surfaces in chat move each topic's score up or down.",
  },
  {
    title: "The roadmap reorders",
    body: "Weak topics rise, and your goal's current focus follows them.",
  },
  {
    title: "Αθηνα checks back",
    body: "Scheduled runs revisit the gaps and record what they found.",
  },
];

function FeatureCard({ feature }: { feature: Feature }) {
  return (
    <li>
      <Link
        href={feature.href}
        className={styles.card}
        style={
          {
            "--accent": feature.accent,
            "--accent-soft": feature.accentSoft,
          } as React.CSSProperties
        }
      >
        <span className={styles.glyph} aria-hidden="true">
          {feature.glyph}
        </span>
        <h3 className={styles.cardTitle}>{feature.title}</h3>
        <p className={styles.cardBody}>{feature.body}</p>
        <span className={styles.cardLink}>
          Open {feature.title} <span aria-hidden="true">→</span>
        </span>
      </Link>
    </li>
  );
}

export default function LandingPage() {
  return (
    <>
      <Nav />

      <div className={styles.page}>
        <header className={styles.hero}>
          <div className={styles.heroInner}>
            <Image
              className={styles.mark}
              src="/logo-mark.png"
              alt=""
              width={460}
              height={320}
              priority
            />
            <p className={styles.eyebrow}>Αθηνα</p>
            <h1 className={styles.heroTitle}>
              A study agent that reads <em>your</em> material.
            </h1>
            <p className={styles.heroBody}>
              Give Αθηνα the slides, notes and readings you were going to study
              anyway. It sorts them into topics, keeps a running score of what you
              understand, and uses that score to decide what is worth your attention
              next.
            </p>
            <div className={styles.heroActions}>
              <Link href="/dashboard" className="btn">
                Open the dashboard
              </Link>
              <Link href="/materials" className="btn-inline ghost">
                Upload material
              </Link>
            </div>
          </div>
        </header>

        <section className={styles.block}>
          <div className={styles.sectionHead}>
            <h2>What it does</h2>
            <span className={styles.rule} aria-hidden="true" />
          </div>
          <ul className={styles.grid}>
            {FEATURES.map((feature) => (
              <FeatureCard key={feature.href} feature={feature} />
            ))}
          </ul>
        </section>

        <section className={styles.block}>
          <div className={styles.sectionHead}>
            <h2>How the pieces feed each other</h2>
            <span className={styles.rule} aria-hidden="true" />
          </div>
          <ol className={styles.loop}>
            {LOOP.map((step, i) => (
              <li key={step.title} className={styles.step}>
                <span className={styles.stepNum}>0{i + 1}</span>
                <p className={styles.stepTitle}>{step.title}</p>
                <p className={styles.stepBody}>{step.body}</p>
              </li>
            ))}
          </ol>
        </section>

        <div className={styles.note}>
          <p>
            <strong>Nothing here is generic coursework.</strong> Every topic, quiz
            question and roadmap step comes from material you uploaded yourself — so
            the score Αθηνα keeps is a score of your syllabus, not someone else&rsquo;s.
          </p>
        </div>
      </div>
    </>
  );
}
