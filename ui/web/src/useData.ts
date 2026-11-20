import { useEffect, useState } from "react";
import { api } from "./api";
import { useProposals } from "./ConfirmCard";

// Loads a server view and reloads it whenever a confirmed change happens anywhere in the app.
export function useData<T>(path: string | null, deps: unknown[] = []): T | null {
  const { version } = useProposals();
  const [data, setData] = useState<T | null>(null);
  useEffect(() => {
    if (!path) return;
    let live = true;
    api<T>(path)
      .then((d) => live && setData(d))
      .catch(() => live && setData(null));
    return () => {
      live = false;
    };
  }, [path, version, ...deps]);
  return data;
}
