"use server";

/**
 * Same reasoning as settings/actions.ts, one extra step further: running
 * gather now needs a secret (`X-Gather-Token`) as well as a server-only
 * network path. `MATERIALS_GATHER_TOKEN` is read here and nowhere else on the
 * frontend -- it must never reach the browser bundle, since the endpoint it
 * guards auto-imports and ingests with no approval step.
 */

import { revalidatePath } from "next/cache";

import {
  ApiError,
  clearGatherFolder,
  type GatherConfig,
  type GatherRunResult,
  runGather,
  setGatherFolder,
} from "@/lib/api";

export type ActionResult<T> = { data: T | null; error: string | null };

function failed<T>(err: unknown, fallback: string): ActionResult<T> {
  return { data: null, error: err instanceof ApiError ? err.message : fallback };
}

export async function runGatherNow(): Promise<ActionResult<GatherRunResult>> {
  const token = process.env.MATERIALS_GATHER_TOKEN;
  if (!token) {
    return {
      data: null,
      error: "MATERIALS_GATHER_TOKEN isn't set in the frontend's environment.",
    };
  }

  let data: GatherRunResult;
  try {
    data = await runGather(token);
  } catch (err) {
    return failed(err, "Could not run sync.");
  }
  revalidatePath("/knowledge-sync");
  return { data, error: null };
}

export async function saveGatherFolder(
  folder: string,
): Promise<ActionResult<GatherConfig>> {
  if (!folder.trim()) {
    return { data: null, error: "Paste a Drive folder link or id first." };
  }

  let data: GatherConfig;
  try {
    data = await setGatherFolder(folder);
  } catch (err) {
    return failed(err, "Could not save that folder.");
  }
  revalidatePath("/knowledge-sync");
  return { data, error: null };
}

export async function clearGatherFolderAction(): Promise<ActionResult<GatherConfig>> {
  let data: GatherConfig;
  try {
    data = await clearGatherFolder();
  } catch (err) {
    return failed(err, "Could not clear the folder scope.");
  }
  revalidatePath("/knowledge-sync");
  return { data, error: null };
}
