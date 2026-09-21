"use server";

import { redirect } from "next/navigation";

import { createSession } from "@/lib/api";

export async function startChat() {
  const session = await createSession("chat");
  redirect(`/sessions/${session.id}`);
}
