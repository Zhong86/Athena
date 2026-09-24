"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useState } from "react";

import {
  getConnections,
  listGoals,
  listQuizzes,
  listSessions,
  listUploads,
} from "@/lib/api";

import styles from "./getStartedGuide.module.css";

const DASHBOARD_VISITED_KEY = "athena:onboarding:dashboard-visited";

type StepKey = "drive" | "sync" | "quiz" | "goal" | "review" | "dashboard";

type Step = {
  key: StepKey;
  title: string;
  description: string;
  href: string;
};

const STEPS: Step[] = [
  {
    key: "drive",
    title: "Connect with Google Drive",
    description: "Link your account so Αθηνα can read your notes.",
    href: "/settings",
  },
  {
    key: "sync",
    title: "Sync materials",
    description: "Pull your files in so there's something to study.",
    href: "/knowledge-sync",
  },
  {
    key: "quiz",
    title: "Create your first quiz",
    description: "Test yourself on what you've uploaded.",
    href: "/quizzes/new",
  },
  {
    key: "goal",
    title: "Create your first goal",
    description: "Set a target and get a roadmap to follow.",
    href: "/goal/new",
  },
  {
    key: "review",
    title: "Review materials from the roadmap with Αθηνα",
    description: "Work through a roadmap milestone together.",
    href: "/goal",
  },
  {
    key: "dashboard",
    title: "Check the dashboard for your weak topics",
    description: "See what she noticed you're struggling with.",
    href: "/dashboard",
  },
];

/**
 * Most steps have a real completion signal (a row exists somewhere), except
 * "check the dashboard" -- there is nothing to persist for looking at a
 * page, so that one is tracked locally as "did the user click through".
 */
async function loadProgress(): Promise<Record<StepKey, boolean>> {
  const [connections, uploads, quizzes, goals, chatSessions] = await Promise.all([
    getConnections().catch(() => null),
    listUploads().catch(() => []),
    listQuizzes({ limit: 1 }).catch(() => null),
    listGoals().catch(() => []),
    listSessions({ type: "chat", limit: 1 }).catch(() => null),
  ]);

  const google = connections?.items.find((c) => c.slug === "google");

  return {
    drive: google?.status === "connected",
    sync: uploads.length > 0,
    quiz: (quizzes?.total ?? 0) > 0,
    goal: goals.length > 0,
    review: (chatSessions?.total ?? 0) > 0,
    dashboard:
      typeof window !== "undefined" && localStorage.getItem(DASHBOARD_VISITED_KEY) === "1",
  };
}

/**
 * The circular FAB used to open a mini chat panel that just duplicated what
 * any session already does. It's now a "Get Started" checklist instead --
 * the one thing this app didn't have and new users actually need.
 */
export function GetStartedGuide() {
  const [open, setOpen] = useState(false);
  const [progress, setProgress] = useState<Record<StepKey, boolean> | null>(null);

  useEffect(() => {
    loadProgress().then(setProgress);
  }, []);

  const doneCount = progress ? Object.values(progress).filter(Boolean).length : 0;

  return (
    <>
      <button
        type="button"
        className={styles.fab}
        aria-label="Open Get Started guide"
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
      >
        <Image
          className={styles.fabMark}
          src="/logo-mark.png"
          alt=""
          width={460}
          height={320}
        />
        {progress ? (
          <span className={styles.badge}>
            {doneCount}/{STEPS.length}
          </span>
        ) : null}
      </button>

      <div className={`${styles.panel} ${open ? styles.panelOpen : ""}`}>
        <div className={styles.panelHead}>
          <span className={styles.name}>Get started</span>
          <button
            type="button"
            className={styles.close}
            aria-label="Close guide"
            onClick={() => setOpen(false)}
          >
            ✕
          </button>
        </div>

        <ol className={styles.list}>
          {STEPS.map((step, index) => {
            const done = progress?.[step.key] ?? false;
            return (
              <li key={step.key} className={done ? styles.itemDone : styles.item}>
                <Link
                  href={step.href}
                  className={styles.itemLink}
                  onClick={() => {
                    if (step.key === "dashboard") {
                      localStorage.setItem(DASHBOARD_VISITED_KEY, "1");
                    }
                    setOpen(false);
                  }}
                >
                  <span className={styles.itemMark} aria-hidden="true">
                    {done ? "✓" : index + 1}
                  </span>
                  <span className={styles.itemBody}>
                    <span className={styles.itemTitle}>{step.title}</span>
                    <span className={styles.itemDescription}>{step.description}</span>
                  </span>
                </Link>
              </li>
            );
          })}
        </ol>
      </div>
    </>
  );
}
