"use server";

/**
 * Connection mutations go through Server Actions rather than straight from the
 * browser, unlike the materials upload next door.
 *
 * The reason is the Caddy basic_auth gate on `/api/connections*`: these are the
 * only endpoints that take a credential, and Athena has no auth layer of its
 * own. A browser `fetch()` gets a bare 401 from that gate rather than a
 * password prompt, so client-side calls would simply fail. Running them on the
 * server sends them to `INTERNAL_API_BASE_URL` on the compose network instead,
 * which never passes through Caddy.
 *
 * The 1MB Server Action body cap that ruled this out for materials is not a
 * factor here -- a client secret file is a couple of KB.
 */

import { revalidatePath } from "next/cache";

import {
  ApiError,
  type AuthorizeStarted,
  type Capability,
  type Connection,
  disconnectGoogle,
  exchangeGoogleCode,
  setCapability,
  uploadGoogleClient,
} from "@/lib/api";

/** Failures render inline in the card, so these return a message rather than
    throwing into the error boundary and blanking the Settings page. */
export type ActionResult<T> = { data: T | null; error: string | null };

function failed<T>(err: unknown, fallback: string): ActionResult<T> {
  return { data: null, error: err instanceof ApiError ? err.message : fallback };
}

export async function startGoogleConnect(
  form: FormData,
): Promise<ActionResult<AuthorizeStarted>> {
  const file = form.get("file");
  if (!(file instanceof File) || file.size === 0) {
    return { data: null, error: "Choose the client secret JSON file first." };
  }

  let data: AuthorizeStarted;
  try {
    data = await uploadGoogleClient(file);
  } catch (err) {
    return failed(err, "Could not read that file.");
  }
  revalidatePath("/settings");
  return { data, error: null };
}

export async function finishGoogleConnect(
  pasted: string,
): Promise<ActionResult<Connection>> {
  if (!pasted.trim()) {
    return { data: null, error: "Paste the URL you were redirected to." };
  }

  let data: Connection;
  try {
    data = await exchangeGoogleCode(pasted);
  } catch (err) {
    return failed(err, "Could not finish connecting.");
  }
  revalidatePath("/settings");
  return { data, error: null };
}

export async function toggleCapability(
  slug: string,
  capability: Capability,
  enabled: boolean,
): Promise<ActionResult<Connection>> {
  let data: Connection;
  try {
    data = await setCapability(slug, capability, enabled);
  } catch (err) {
    return failed(err, "Could not change that permission.");
  }
  revalidatePath("/settings");
  return { data, error: null };
}

/** Resolves with a warning when the local clear succeeded but revoking at
    Google or removing the files from the Hermes host did not -- the user is
    disconnected either way and should know it was untidy. */
export async function disconnectGoogleAction(): Promise<
  ActionResult<Connection> & { warning?: string | null }
> {
  try {
    const result = await disconnectGoogle();
    revalidatePath("/settings");
    return { data: result.connection, error: null, warning: result.warning };
  } catch (err) {
    return failed(err, "Could not disconnect.");
  }
}
