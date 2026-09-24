import { useCallback, useEffect, useState } from "react";
import { ListChecks, Play, Clock, CircleDot, X } from "lucide-react";
import { Button } from "./ui/button";

type QueueEntry = {
  name: string;
  state: "running" | "due" | "waiting" | "off";
  seconds: number;
  reason: string;
  running_for: number;
};

type Instance = {
  name: string | null;
  /** null where liveness could not be established -- shown as unknown, never guessed. */
  running: boolean | null;
  /** This process's own emulator. Only its row gets controls; see the buttons below. */
  local: boolean;
  device: string;
  hold: { seconds: number; reason: string } | null;
  queue: QueueEntry[];
};

type Status = {
  running: boolean;
  hotkey: string;
  instances: Instance[];
};

/** "4h 12m", "3m 20s" -- the same shape the bot writes in its own log. */
function describe(seconds: number) {
  if (seconds <= 0) return "now";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours) return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  return `${minutes}m ${String(Math.floor(seconds % 60)).padStart(2, "0")}s`;
}

/** Task names are ids; this is what a person calls them. */
const LABELS: Record<string, string> = {
  career: "Career",
  team_trials: "Team Trials",
  missions: "Mission Rewards",
  present_box: "Present Box",
  daily_races: "Daily Races",
};

const STATE_STYLE: Record<QueueEntry["state"], string> = {
  running: "bg-primary/15 text-primary border-primary/30",
  due: "bg-muted text-muted-foreground border-border",
  waiting: "bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/30",
  off: "bg-transparent text-muted-foreground/60 border-border/50",
};

/**
 * What the queue is doing: Running, Due, Waiting.
 *
 * The rows come from the schedule file, so this reads correctly whether or not the bot
 * is running -- a stopped bot still has cooldowns, and seeing "Team Trials, 28m" is the
 * answer to "why is it not racing" without opening a log.
 *
 * Rendered from a list of instances that happens to have one entry. Phase 5 gives the
 * machine more than one emulator, and that should be a matter of more rows rather than
 * a rewrite of this file.
 */
export default function OverviewSection() {
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // Control writes act on this process's scheduler and can refuse -- 503 before the
  // bot's controls are registered, 404 for a task this instance does not have. They
  // used to be swallowed, so the button simply looked dead.
  const [controlError, setControlError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch("/bot/status", { cache: "no-store" });
      if (response.ok) setStatus((await response.json()) as Status);
    } catch {
      // Same process as the bot: a failure here means the whole thing is gone, and the
      // last known state is more use than a blank panel.
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 2000);
    return () => clearInterval(timer);
  }, [refresh]);

  const clearHold = async () => {
    setBusy("__hold");
    setControlError(null);
    try {
      const res = await fetch("/hold/clear", { method: "POST" });
      if (!res.ok) {
        setControlError(`Could not clear the hold: ${res.status} ${res.statusText}`);
      }
      await refresh();
    } catch {
      setControlError("Could not reach this instance to clear the hold.");
    } finally {
      setBusy(null);
    }
  };

  const runNow = async (task: string) => {
    setBusy(task);
    setControlError(null);
    try {
      const res = await fetch(`/task/${task}/run-now`, { method: "POST" });
      if (!res.ok) {
        // The server says which of its two 404s this is -- no schedule yet, or no such
        // task -- and the status line alone threw that away. Read the way BotControl does.
        let detail = `${res.status} ${res.statusText}`;
        try {
          const body = await res.json();
          if (body?.detail) detail = String(body.detail);
        } catch {
          // no JSON body; the status is all there is to say
        }
        setControlError(`Could not start ${task}: ${detail}`);
      }
      await refresh();
    } catch {
      setControlError(`Could not reach this instance to start ${task}.`);
    } finally {
      setBusy(null);
    }
  };

  const instances = status?.instances ?? [];

  return (
    <div className="section-card">
      <h2 className="text-3xl font-semibold mb-4 flex items-center gap-3">
        <ListChecks className="text-primary" />
        Overview
      </h2>

      <p className="text-sm text-muted-foreground mb-6">
        The task queue, as the bot sees it. A task that is waiting says what it is waiting
        for and how long is left; <strong>Run now</strong> clears that wait and the bot
        picks the task up on its next pass.
      </p>

      {status === null && (
        <p className="text-sm text-muted-foreground">Reading the queue&hellip;</p>
      )}

      {instances.map((instance) => (
        <div key={instance.device ?? instance.name ?? "desktop"} className="mb-6">
          <div className="flex items-center gap-2 mb-3">
            <CircleDot
              size={16}
              className={instance.running ? "text-primary" : "text-muted-foreground"}
            />
            <span className="font-medium">{instance.name ?? "desktop"}</span>
            <span className="text-sm text-muted-foreground">
              {instance.running === null
                ? "state unknown"
                : instance.running
                  ? "running"
                  : "stopped"}
            </span>
            {!instance.local && (
              <span
                className="text-xs text-muted-foreground/80 border border-border rounded px-1.5 py-0.5"
                title="Read from this emulator's state file. Open its own page to control it."
              >
                another instance
              </span>
            )}
          </div>

          {instance.hold && (
            <div className="mb-3 flex items-center gap-2.5 rounded-md border border-amber-500/40
                            bg-amber-500/10 px-3 py-2 text-sm">
              <Clock size={16} className="shrink-0 text-amber-600 dark:text-amber-400" />
              <span className="flex-1">
                <strong>Everything is held for {describe(instance.hold.seconds)}</strong>
                {instance.hold.reason ? ` — ${instance.hold.reason}.` : "."} Nothing below
                will run until it lifts.
              </span>
              {instance.local && (
                <Button
                  variant="outline"
                  size="sm"
                  className="uma-btn shrink-0"
                  disabled={busy === "__hold"}
                  onClick={() => void clearHold()}
                  title="Lift the hold now and carry on"
                >
                  <X size={14} className="mr-1" />
                  Clear
                </Button>
              )}
            </div>
          )}

          {instance.local && controlError && (
            <p className="text-sm text-red-500 mb-2">{controlError}</p>
          )}

          {instance.queue.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No tasks yet &mdash; start the bot and the queue fills itself in.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <tbody>
                  {instance.queue.map((task) => (
                    <tr
                      key={task.name}
                      className={`border-b border-border last:border-0 ${
                        task.state === "off" ? "opacity-50" : ""
                      }`}
                    >
                      <td className="py-2.5 pr-4 font-medium whitespace-nowrap">
                        {LABELS[task.name] ?? task.name}
                      </td>
                      <td className="py-2.5 pr-4 w-24">
                        <span
                          className={`inline-block px-2 py-0.5 rounded-full border text-xs
                                      font-medium capitalize ${STATE_STYLE[task.state]}`}
                        >
                          {task.state}
                        </span>
                      </td>
                      <td className="py-2.5 pr-4 whitespace-nowrap tabular-nums
                                     text-muted-foreground w-28">
                        {task.state === "waiting" && instance.local && (
                          <span className="inline-flex items-center gap-1.5">
                            <Clock size={14} />
                            {describe(task.seconds)}
                          </span>
                        )}
                        {task.state === "running" && task.running_for > 0 &&
                          `for ${describe(task.running_for)}`}
                      </td>
                      <td className="py-2.5 pr-4 text-muted-foreground">
                        {task.state === "off" ? "switched off in Automation" : task.reason}
                      </td>
                      <td className="py-2.5 text-right w-28">
                        {task.state === "waiting" && instance.local && (
                          <Button
                            variant="outline"
                            size="sm"
                            className="uma-btn"
                            disabled={busy === task.name}
                            onClick={() => void runNow(task.name)}
                          >
                            <Play size={14} className="mr-1" />
                            Run now
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
