"use client";

import { createContext, useCallback, useContext, useMemo, useState } from "react";

import { ApiError, type RoadmapEnvelope } from "@/lib/api";

type CreationState =
  | { status: "idle" }
  | { status: "creating"; label: string }
  | { status: "ready"; envelope: RoadmapEnvelope }
  | { status: "error"; message: string };

type RoadmapCreationContextValue = {
  state: CreationState;
  /** Wraps a roadmap-graph call so its progress survives the caller
      unmounting — the student can navigate elsewhere and the banner still
      picks up the result when the request finally resolves. */
  track: (label: string, work: () => Promise<RoadmapEnvelope>) => Promise<RoadmapEnvelope>;
  dismiss: () => void;
};

const RoadmapCreationContext = createContext<RoadmapCreationContextValue | null>(null);

export function RoadmapCreationProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<CreationState>({ status: "idle" });

  const track = useCallback((label: string, work: () => Promise<RoadmapEnvelope>) => {
    setState({ status: "creating", label });
    return work().then(
      (envelope) => {
        setState({ status: "ready", envelope });
        return envelope;
      },
      (err) => {
        setState({
          status: "error",
          message: err instanceof ApiError ? err.message : "Could not build that roadmap.",
        });
        throw err;
      },
    );
  }, []);

  const dismiss = useCallback(() => setState({ status: "idle" }), []);

  const value = useMemo(() => ({ state, track, dismiss }), [state, track, dismiss]);

  return (
    <RoadmapCreationContext.Provider value={value}>{children}</RoadmapCreationContext.Provider>
  );
}

export function useRoadmapCreation() {
  const ctx = useContext(RoadmapCreationContext);
  if (!ctx) throw new Error("useRoadmapCreation must be used within RoadmapCreationProvider");
  return ctx;
}
