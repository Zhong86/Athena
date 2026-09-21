import { RoadmapWizard } from "./RoadmapWizard";

export const metadata = { title: "New goal · Αθηνα" };

/**
 * The creation flow is its own chrome: no rail, no chat FAB, one "Save and
 * exit" link — the mockup treats it as a focused flow, and the graph is
 * interrupt-driven, so wandering off mid-run is what the thread id is for.
 */
export default async function NewGoalPage(props: {
  searchParams: Promise<{ thread?: string }>;
}) {
  const { thread } = await props.searchParams;
  return <RoadmapWizard threadId={thread ?? null} />;
}
