import { useEffect, useState } from "react";
import { ArrowUpCircle, X } from "lucide-react";

import UpdateDialog from "./UpdateDialog";

export type UpdateStatus = {
  current: string;
  commit: string;
  latest: string;
  behind: boolean;
  notes: string;
  url: string;
  checked_at: number;
  error: string;
  enabled: boolean;
};

export async function fetchUpdateStatus(refresh = false): Promise<UpdateStatus | null> {
  try {
    const res = await fetch(`/update/status${refresh ? "?refresh=true" : ""}`, { cache: "no-store" });
    if (!res.ok) throw new Error(String(res.status));
    return (await res.json()) as UpdateStatus;
  } catch {
    return null;
  }
}

// Dismissal is per version, so waving this away does not also hide the next release.
const dismissedKey = "updateDismissed";

export function useUpdateStatus() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  useEffect(() => {
    let alive = true;
    fetchUpdateStatus().then((found) => {
      if (alive) setStatus(found);
    });
    return () => {
      alive = false;
    };
  }, []);
  return status;
}

export default function UpdateBanner({ status }: { status: UpdateStatus | null }) {
  const [dismissed, setDismissed] = useState<string>(() => {
    try {
      return localStorage.getItem(dismissedKey) ?? "";
    } catch {
      return "";
    }
  });
  const [showNotes, setShowNotes] = useState(false);
  const [updating, setUpdating] = useState(false);

  if (!status || !status.behind) return null;
  if (dismissed === status.latest) return null;

  const dismiss = () => {
    try {
      localStorage.setItem(dismissedKey, status.latest);
    } catch {
      // A browser refusing storage costs a banner that comes back on reload, nothing more.
    }
    setDismissed(status.latest);
  };

  return (
    <div className="w-full rounded-md border border-primary/40 bg-primary/10 px-4 py-2 flex flex-col gap-2">
      <div className="flex items-center gap-3">
        <ArrowUpCircle size={16} className="text-primary shrink-0" />
        <span className="text-sm">
          <span className="font-semibold">Version {status.latest}</span> is out — you are on{" "}
          {status.current}.
        </span>
        {/* The button only opens the dialog. Nothing updates without a second press,
            and the dialog says what would stop it before offering to try. */}
        <button
          className="text-xs rounded bg-primary text-primary-foreground px-2 py-1 cursor-pointer hover:bg-primary/90"
          onClick={() => setUpdating(true)}
        >
          Update
        </button>
        {status.notes && (
          <button
            className="text-xs underline text-muted-foreground hover:text-foreground cursor-pointer"
            onClick={() => setShowNotes(!showNotes)}
          >
            {showNotes ? "Hide changes" : "What changed"}
          </button>
        )}
        <a
          className="text-xs underline text-muted-foreground hover:text-foreground"
          href={status.url}
          target="_blank"
          rel="noreferrer"
        >
          GitHub
        </a>
        <button
          className="ml-auto text-muted-foreground hover:text-foreground cursor-pointer"
          onClick={dismiss}
          title="Hide until the next version"
        >
          <X size={14} />
        </button>
      </div>
      {showNotes && status.notes && (
        <pre className="text-xs whitespace-pre-wrap max-h-60 overflow-y-auto text-muted-foreground m-0">
          {status.notes}
        </pre>
      )}
      <UpdateDialog open={updating} onOpenChange={setUpdating} latest={status.latest} />
    </div>
  );
}
