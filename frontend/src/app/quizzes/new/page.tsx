import { QuizWizard } from "./QuizWizard";

export const metadata = { title: "New quiz · Αθηνα" };

/**
 * Same posture as `/goal/new`: its own chrome, no rail, one "Save and exit"
 * link — the graph is interrupt-driven, so wandering off mid-run is what the
 * thread id in the URL is for.
 */
export default async function NewQuizPage(props: {
  searchParams: Promise<{ thread?: string }>;
}) {
  const { thread } = await props.searchParams;
  return <QuizWizard threadId={thread ?? null} />;
}
