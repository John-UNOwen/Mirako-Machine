import { useCallback, useEffect, useState } from "react";
import { Play, Square, Flag } from "lucide-react";
import { Button } from "./ui/button";

type Status = {
  running: boolean;
  stop_after_career: boolean;
  hotkey: string;
};

/**
 * Start and stop the bot from the browser.
 *
 * The hotkey still works and is still the emergency stop -- it does not need the browser
 * focused. What it cannot be any more is the only way in: it fires whatever window has
 * focus, f1..f10 runs out, and the instance it names comes from whichever port happened
 * to be free at startup.
 *
 * Polled rather than pushed. The bot stops on its own for reasons this page never hears
 * about -- a career cap, a stuck screen, someone's F1 -- so the button has to follow the
 * bot rather than remember what it last asked for.
 */
export default function BotControl() {
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch("/bot/status", { cache: "no-store" });
      if (response.ok) setStatus((await response.json()) as Status);
    } catch {
      // The server is in the same process as the bot, so a failure here means the whole
      // thing is gone. Leaving the last known state up is better than flickering.
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 2000);
    return () => clearInterval(timer);
  }, [refresh]);

  const [controlError, setControlError] = useState<string | null>(null);

  const post = async (path: string) => {
    setBusy(true);
    setControlError(null);
    try {
      // 503 until main.py has registered the bot's controls, and 500 for anything the
      // handler throws. Both were swallowed, so Start simply looked like a dead button.
      const res = await fetch(path, { method: "POST" });
      if (!res.ok) {
        let detail = `${res.status} ${res.statusText}`;
        try {
          const body = await res.json();
          if (body?.detail) detail = String(body.detail);
        } catch {
          // no JSON body; the status is all there is to say
        }
        setControlError(detail);
      }
      await refresh();
    } catch {
      setControlError("Could not reach the bot's server.");
    } finally {
      setBusy(false);
    }
  };

  const running = status?.running ?? false;
  const armed = status?.stop_after_career ?? false;
  const hotkey = (status?.hotkey ?? "f1").toUpperCase();

  return (
    <div className="flex items-center gap-2">
      {controlError && (
        <span className="text-xs text-red-500 max-w-64" title={controlError}>
          {controlError}
        </span>
      )}
      {running && (
        <Button
          variant={armed ? "default" : "outline"}
          className="uma-btn font-medium"
          disabled={busy}
          onClick={() => void post("/bot/stop-after-career")}
          title={
            armed
              ? "Armed: the bot stops once this career finishes. Click to cancel."
              : "Finish the career in progress, then stop."
          }
        >
          <Flag size={16} className="mr-1" />
          {armed ? "Stopping after this career" : "Finish & stop"}
        </Button>
      )}
      <Button
        className={`uma-btn font-bold ${
          running
            ? "bg-destructive text-white hover:bg-destructive/90"
            : "bg-primary text-primary-foreground"
        }`}
        disabled={busy || status === null}
        onClick={() => void post(running ? "/bot/stop" : "/bot/start")}
        title={`Or press ${hotkey}`}
      >
        {running ? <Square size={16} className="mr-1" /> : <Play size={16} className="mr-1" />}
        {running ? "Stop" : "Start"}
      </Button>
    </div>
  );
}
