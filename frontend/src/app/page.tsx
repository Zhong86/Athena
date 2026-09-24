import Image from "next/image";
import Link from "next/link";

import styles from "./landing.module.css";

export const metadata = {
  title: "Αθηνα — a study agent that reads your own material",
  description:
    "Upload your material, set a goal, and let Αθηνα track what you actually understand.",
};

/**
 * The front door. No rail here on purpose: "/" is not a destination inside the
 * app, it is the way in, so the only control on the page is the one that takes
 * you to the dashboard.
 *
 * The art is composed with Αθηνα on the right and an empty indigo field on the
 * left, so the copy sits in that field rather than on top of her. It goes
 * through next/image (not a CSS background) because the source is a 1.1MB PNG
 * and this is the first paint -- `fill` + `sizes` gets it served as a
 * viewport-sized AVIF/WebP instead.
 */
export default function LandingPage() {
  return (
    <main className={styles.page}>
      {/* The image is wrapped rather than positioned directly: `fill` writes
          inset/height as inline styles, which beat any class rule, so the
          portrait layout resizes this box and lets the image fill it. */}
      <div className={styles.artWrap} aria-hidden="true">
        <Image
          className={styles.art}
          src="/athena_background.png"
          alt=""
          fill
          sizes="100vw"
          priority
        />
      </div>
      <div className={styles.scrim} aria-hidden="true" />

      <div className={styles.inner}>
        {/* Decorative: the wordmark directly below already names the app, so
            announcing the mark too would just repeat it. */}
        <Image
          className={styles.mark}
          src="/logo-mark.png"
          alt=""
          width={460}
          height={320}
          priority
        />

        <h1 className={styles.wordmark}>Αθηνα</h1>

        <p className={styles.tagline}>
          A study agent that guides you with your materials. 
          She uses various tools to help you achieve your academic goals. 
          Sync with Google Drive for your notes, generate quizzes, and even roadmaps for you to follow. 
        </p>

        <Link href="/dashboard" className={styles.cta}>
          Enter
          <span className={styles.arrow} aria-hidden="true">
            →
          </span>
        </Link>
      </div>
    </main>
  );
}
