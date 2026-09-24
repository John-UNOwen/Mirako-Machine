import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, RotateCcw } from "lucide-react";

import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "./ui/dialog";

export type Refusal = {
  code: string;
  message: string;
  files?: string[];
  branch?: string;
};

export type Preflight = {
  ready: boolean;
  refusals: Refusal[];
  branch: string;
  commit: string;
  checkout: boolean;
  releases_url: string;
  can_roll_back: boolean;
  rollback_to: string;
};

type Outcome = {
  status: string;
  from?: string;
  to?: string;
  restart_required?: boolean;
  requirements_installed?: boolean;
  detached?: boolean;
  return_command?: string;
  log?: string;
};

async function readError(res: Response): Promise<{ message: string; log: string }> {
  try {
    const body = await res.json();
    const detail = body?.detail ?? body;
    return {
      message: typeof detail === "string" ? detail : detail?.message || "The update failed.",
      log: typeof detail === "object" ? detail?.log || "" : "",
    };
  } catch {
    return { message: `The update failed (HTTP ${res.status}).`, log: "" };
  }
}

export default function UpdateDialog({
  open,
  onOpenChange,
  latest,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  latest: string;
}) {
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [busy, setBusy] = useState<"" | "update" | "rollback">("");
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [failure, setFailure] = useState<{ message: string; log: string } | null>(null);

  const check = useCallback(async () => {
    try {
      const res = await fetch("/update/preflight", { cache: "no-store" });
      if (!res.ok) throw new Error(String(res.status));
      setPreflight((await res.json()) as Preflight);
    } catch {
      setPreflight(null);
    }
  }, []);

  // Re-checked every time the dialog opens, never cached: the two things most likely to
  // block an update -- the bot running, a file edited -- are exactly the things that
  // change between opening this and last looking.
  useEffect(() => {
    if (!open) return;
    setOutcome(null);
    setFailure(null);
    void check();
  }, [open, check]);

  const run = async (what: "update" | "rollback") => {
    setBusy(what);
    setFailure(null);
    try {
      const res = await fetch(what === "update" ? "/update/apply" : "/update/rollback", {
        method: "POST",
      });
      if (!res.ok) {
        setFailure(await readError(res));
        void check();
        return;
      }
      setOutcome((await res.json()) as Outcome);
    } catch (error) {
      setFailure({ message: `Could not reach the bot: ${String(error)}`, log: "" });
    } finally {
      setBusy("");
    }
  };

  // Phase 3. New Python routes only exist in a process that started with them, so once
  // the files have moved this process is serving a page that will call routes it does not
  // have. Everything stays disabled behind this message rather than letting someone keep
  // clicking around a half-updated process.
  const finished = Boolean(outcome?.restart_required);
  const rolledBack = outcome?.status === "rolled_back";

  return (
    <Dialog open={open} onOpenChange={(next) => (finished ? undefined : onOpenChange(next))}>
      <DialogContent className="sm:max-w-xl" showCloseButton={!finished}>
        <DialogHeader>
          <DialogTitle>
            {finished ? (rolledBack ? "Rolled back" : "Update installed") : "Update the bot"}
          </DialogTitle>
          <DialogDescription>
            {finished
              ? rolledBack
                ? "The files are back on the recorded version, but this window is still running the new one."
                : "The files have changed, but this window is still running the old version."
              : latest
                ? `Version ${latest} is available.`
                : "Pull the newest version from GitHub."}
          </DialogDescription>
        </DialogHeader>

        {finished ? (
          <div className="flex flex-col gap-3">
            {outcome?.requirements_installed === false && (
              <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm">
                <AlertTriangle size={16} className="text-destructive mt-0.5 shrink-0" />
                <p className="m-0 whitespace-pre-wrap">
                  The environment may be half-installed -- run{" "}
                  <code className="rounded bg-card px-1 py-0.5 text-xs">pip install -r requirements.txt</code>{" "}
                  in the bot folder before starting.
                </p>
              </div>
            )}
            <div className="flex items-start gap-2 rounded-md border border-primary/40 bg-primary/10 p-3">
              <CheckCircle2 size={16} className="text-primary mt-0.5 shrink-0" />
              <div className="text-sm">
                <p className="m-0 font-semibold">
                  Close this window and run start.bat again to finish.
                </p>
                <p className="m-0 mt-1 text-muted-foreground">
                  {outcome?.detached
                    ? `You are now on ${outcome?.to || "the previous version"}, which is a single version rather than the latest. To come back to the latest later, run ${outcome?.return_command} in the bot folder.`
                    : `Updated ${outcome?.from || "?"} → ${outcome?.to || "?"}.`}
                </p>
              </div>
            </div>
            {outcome?.log && (
              <pre className="m-0 max-h-60 overflow-y-auto rounded-md bg-card p-3 text-xs whitespace-pre-wrap text-muted-foreground">
                {outcome.log}
              </pre>
            )}
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {preflight && !preflight.checkout && (
              <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm">
                <p className="m-0">
                  This copy was downloaded as a zip, so it cannot update itself.
                </p>
                <a
                  className="underline"
                  href={preflight.releases_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  Download the latest zip
                </a>
              </div>
            )}

            {preflight?.refusals?.filter((r) => r.code !== "not_a_checkout").map((refusal) => (
              <div
                key={refusal.code}
                className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm"
              >
                <AlertTriangle size={16} className="text-destructive mt-0.5 shrink-0" />
                <p className="m-0 whitespace-pre-wrap">{refusal.message}</p>
              </div>
            ))}

            {preflight?.ready && (
              <p className="m-0 text-sm text-muted-foreground">
                On {preflight.branch} at {preflight.commit}. Your config, logs and
                presets are not touched.
              </p>
            )}

            {failure && (
              <div className="flex flex-col gap-2">
                <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm">
                  <AlertTriangle size={16} className="text-destructive mt-0.5 shrink-0" />
                  <p className="m-0 whitespace-pre-wrap">{failure.message}</p>
                </div>
                {failure.log && (
                  <pre className="m-0 max-h-48 overflow-y-auto rounded-md bg-card p-3 text-xs whitespace-pre-wrap text-muted-foreground">
                    {failure.log}
                  </pre>
                )}
              </div>
            )}

            <div className="flex items-center gap-2">
              <Button disabled={!preflight?.ready || busy !== ""} onClick={() => void run("update")}>
                {busy === "update" && <Loader2 size={14} className="animate-spin" />}
                {busy === "update" ? "Updating…" : "Update now"}
              </Button>
              {preflight?.can_roll_back && (
                <Button
                  variant="outline"
                  disabled={busy !== ""}
                  onClick={() => void run("rollback")}
                  title={`Go back to ${preflight.rollback_to || "the previous version"}`}
                >
                  {busy === "rollback" ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <RotateCcw size={14} />
                  )}
                  Go back to {preflight.rollback_to || "the previous version"}
                </Button>
              )}
              {!preflight?.ready && preflight?.checkout && (
                <Button variant="ghost" onClick={() => void check()} disabled={busy !== ""}>
                  Check again
                </Button>
              )}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
