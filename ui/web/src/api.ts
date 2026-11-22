// Thin wrapper over the Python server. The server filters by level; the browser never gets more than it may see.

let member = "";
export function setMember(id: string) {
  member = id;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T = any>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "X-Member": member, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    let msg = r.statusText;
    try {
      msg = (await r.json()).detail ?? msg;
    } catch {}
    throw new ApiError(r.status, msg);
  }
  return r.json();
}

export const usd = (cents: number) =>
  (cents / 100).toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: cents >= 100000 ? 0 : 2 });

export type Member = { id: string; name: string; title: string; language: "en" | "es" };
export type Me = Member & { level: number; level_name: string; menu: { id: string; label: string }[]; today: string; clock: number };
export type Proposal = {
  id: number;
  kind: string;
  params: Record<string, any>;
  title: string;
  lines: string[];
  money?: boolean;
  fix: { field: string; label: string; type: "number" | "text"; min?: number; max?: number }[];
  state: "open" | "confirmed" | "cancelled";
  result?: string;
};
