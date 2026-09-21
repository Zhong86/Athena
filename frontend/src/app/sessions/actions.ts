"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { ApiError, createSession, deleteSession, updateSession } from "@/lib/api";

export async function startChat() {
  const session = await createSession("chat");
  redirect(`/sessions/${session.id}`);
}

/** The row menu reports failures inline, so these return a message rather
    than throwing into the error boundary and losing the whole list. */
export type RowActionResult = { error: string | null };

function failed(err: unknown): RowActionResult {
  return { error: err instanceof ApiError ? err.message : "Something went wrong." };
}

export async function renameChat(id: number, title: string): Promise<RowActionResult> {
  const trimmed = title.trim();
  if (!trimmed) return { error: "Give the chat a name." };
  try {
    await updateSession(id, { title: trimmed });
  } catch (err) {
    return failed(err);
  }
  revalidatePath("/sessions");
  return { error: null };
}

export async function archiveChat(id: number): Promise<RowActionResult> {
  try {
    await updateSession(id, { archived: true });
  } catch (err) {
    return failed(err);
  }
  revalidatePath("/sessions");
  return { error: null };
}

export async function deleteChat(id: number): Promise<RowActionResult> {
  try {
    await deleteSession(id);
  } catch (err) {
    return failed(err);
  }
  revalidatePath("/sessions");
  return { error: null };
}
