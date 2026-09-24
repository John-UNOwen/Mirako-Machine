import { useCallback, useEffect, useRef, useState } from "react";
import { FileText, MonitorSmartphone, Play, Plus, Square } from "lucide-react";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";
import { presetLabel, tabLabel, usersOf } from "../lib/instances";

export { presetLabel, tabLabel, usersOf };

// Which instance this page is talking to, and the others it can switch to. Every instance
// serves the same UI on its own port, so without this two tabs on :8000 and :8001 are
// indistinguishable -- and a setting changed in the wrong one changes the wrong emulator.
export type InstanceInfo = {
  declared: boolean;
  name: string;
  port: number | null;
  hotkey: string | null;
  use_adb: boolean | null;
  device_id: string | null;
  device_from_command_line: boolean;
  config_file: string;
  preset_id?: string;
  preset_name?: string | null;
  // The version this process started on (the boot stamp), reported by /instance. Only
  // the current tab shows it -- each sibling is served by its own process, which alone
  // knows what it stamped at startup. Optional: a server that predates the field
  // simply does not show one.
  version?: string;
};

export type InstanceRow = InstanceInfo & { current: boolean; running: boolean };

export function useInstance(): InstanceInfo | null {
  const [instance, setInstance] = useState<InstanceInfo | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetch("/instance", { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!cancelled && data) setInstance(data);
      })
      .catch(() => {
        // Same process as the page; an older server without the route just shows nothing.
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return instance;
}

// Polled rather than fetched once: an instance started or closed in another window
// should appear or grey out here without a reload.
const POLL_MS = 10_000;

export function useInstances(): { rows: InstanceRow[]; reload: () => Promise<InstanceRow[]> } {
  const [rows, setRows] = useState<InstanceRow[]>([]);
  const alive = useRef(true);
  const reload = useCallback(
    () =>
      fetch("/instances/live", { cache: "no-store" })
        .then((res) => (res.ok ? res.json() : null))
        .then((data) => {
          const next: InstanceRow[] = data?.instances ?? [];
          if (alive.current && data?.instances) setRows(next);
          return next;
        })
        .catch(() => [] as InstanceRow[]),
    [],
  );
  useEffect(() => {
    alive.current = true;
    const load = () => void reload();
    load();
    const timer = window.setInterval(load, POLL_MS);
    return () => {
      alive.current = false;
      window.clearInterval(timer);
    };
  }, [reload]);
  return { rows, reload };
}

// Where switching to another instance goes. Its own page, rather than this page talking
// to its port: this page holds the current instance's unsaved state, and re-pointing its
// requests mid-save would write one instance's settings into another. The section and
// the light/dark choice travel along in the query, since each port keeps its own storage.
export function switchUrl(port: number, tab: string, dark: boolean): string {
  const { protocol, hostname } = window.location;
  const query = new URLSearchParams({ tab, theme: dark ? "dark" : "light" });
  return `${protocol}//${hostname}:${port}/?${query.toString()}`;
}

export const NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,23}$/;

// A launched instance takes a few seconds to import, load its OCR and open its port.
// Watched this long before the tab stops saying "starting".
const LAUNCH_WATCH_MS = 60_000;
const LAUNCH_POLL_MS = 1_500;

async function detail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return body?.detail ?? `HTTP ${res.status}`;
  } catch {
    return `HTTP ${res.status}`;
  }
}

type DeviceRow = { serial: string; state: string; used_by: string[]; same_as?: string | null };

const LOG_POLL_MS = 3_000;

// The log route's name for an instance: its own, or "default" for the unnamed one.
const logKey = (row: InstanceInfo) => (row.declared ? row.name : "default");

type Props = {
  instance: InstanceInfo | null;
  rows: InstanceRow[];
  reload: () => Promise<InstanceRow[]>;
  // Flushes a pending auto-save, so nothing typed a moment ago is lost on the way out.
  // Resolves false when this page's edits could not be saved.
  beforeSwitch: () => Promise<boolean>;
  tab: string;
  dark: boolean;
};

export default function InstanceBanner({ instance, rows, reload, beforeSwitch, tab, dark }: Props) {
  const [switching, setSwitching] = useState<number | null>(null);
  const [busy, setBusy] = useState<Record<string, "starting" | "stopping">>({});
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDevice, setNewDevice] = useState("");
  const [creating, setCreating] = useState(false);
  const [devices, setDevices] = useState<DeviceRow[] | null>(null);
  const [scanning, setScanning] = useState(false);
  const [probe, setProbe] = useState<{ status: string; detail: string } | null>(null);
  const [probing, setProbing] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const [logName, setLogName] = useState<string>("");
  const [logKind, setLogKind] = useState<"log" | "console">("log");
  const [logText, setLogText] = useState<string | null>(null);
  const others = rows.filter((row) => !row.current);

  const loadDevices = useCallback(async (scan: boolean) => {
    if (scan) setScanning(true);
    try {
      const res = await fetch(`/adb/devices${scan ? "?scan=true" : ""}`, { cache: "no-store" });
      const data = res.ok ? await res.json() : { devices: [] };
      setDevices(data.devices ?? []);
    } catch {
      setDevices([]);
    } finally {
      setScanning(false);
    }
  }, []);

  // Listed each time the dialog opens: emulators come and go between one add and the next.
  useEffect(() => {
    if (!adding) return;
    setDevices(null);
    setProbe(null);
    void loadDevices(false);
  }, [adding, loadDevices]);

  const testDevice = useCallback(async () => {
    setProbing(true);
    setProbe(null);
    try {
      const res = await fetch("/adb/probe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_id: newDevice.trim() }),
      });
      setProbe(res.ok ? await res.json() : { status: "fail", detail: await detail(res) });
    } catch (err) {
      setProbe({ status: "fail", detail: err instanceof Error ? err.message : String(err) });
    } finally {
      setProbing(false);
    }
  }, [newDevice]);

  // The log view follows the file while it is open, so a launch that fails can be
  // watched failing rather than guessed at.
  useEffect(() => {
    if (!logsOpen || !logName) return;
    let cancelled = false;
    const load = () =>
      fetch(`/instances/${encodeURIComponent(logName)}/log?kind=${logKind}&lines=300`, {
        cache: "no-store",
      })
        .then(async (res) => (res.ok ? res.json() : { exists: false, text: await detail(res) }))
        .then((data) => {
          if (!cancelled) setLogText(data.exists ? data.text : data.text || null);
        })
        .catch(() => {});
    setLogText(null);
    void load();
    const timer = window.setInterval(load, LOG_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [logsOpen, logName, logKind]);

  const switchTo = useCallback(
    async (port: number) => {
      setSwitching(port);
      // Leaving the page loses whatever it has not saved, so a failed save asks first, the
      // way switching presets does. It used to navigate regardless: a save that failed
      // took the edit with it.
      let saved = false;
      try {
        saved = await beforeSwitch();
      } catch {
        saved = false;
      }
      if (!saved && !window.confirm(
        "Your latest changes on this page could not be saved. Switch anyway and lose them?",
      )) {
        setSwitching(null);
        return;
      }
      window.location.href = switchUrl(port, tab, dark);
    },
    [beforeSwitch, tab, dark],
  );

  // Poll until `name` reports `running` as wanted, or give up after a minute.
  const watch = useCallback(
    async (name: string, running: boolean) => {
      const deadline = Date.now() + LAUNCH_WATCH_MS;
      while (Date.now() < deadline) {
        await new Promise((resolve) => window.setTimeout(resolve, LAUNCH_POLL_MS));
        const next = await reload();
        if (next.some((row) => row.name === name && row.running === running)) return true;
      }
      return false;
    },
    [reload],
  );

  const launch = useCallback(
    async (name: string) => {
      setError(null);
      setBusy((prev) => ({ ...prev, [name]: "starting" }));
      try {
        const res = await fetch(`/instances/${encodeURIComponent(name)}/launch`, { method: "POST" });
        if (!res.ok) throw new Error(await detail(res));
        // The server waits for the worker to answer before replying. "running" is done;
        // "starting" means it is alive but still loading, so the tab goes on watching.
        const outcome = (await res.json())?.status;
        if (outcome === "running") {
          await reload();
        } else if (!(await watch(name, true))) {
          throw new Error(`${name} did not start within a minute. Its log is logs/${name}/console.log.`);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(({ [name]: _, ...rest }) => rest);
      }
    },
    [watch, reload],
  );

  const stop = useCallback(
    async (row: InstanceRow) => {
      if (!row.port) return;
      const ok = window.confirm(
        `Close ${row.name}? Its bot stops and the process ends. A career in progress keeps ` +
          "going in the game and is picked up again when the instance next runs.",
      );
      if (!ok) return;
      setError(null);
      setBusy((prev) => ({ ...prev, [row.name]: "stopping" }));
      try {
        const { protocol, hostname } = window.location;
        const res = await fetch(`${protocol}//${hostname}:${row.port}/instance/shutdown`, {
          method: "POST",
        });
        if (!res.ok) throw new Error(await detail(res));
        await watch(row.name, false);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(({ [row.name]: _, ...rest }) => rest);
      }
    },
    [watch],
  );

  const create = useCallback(async () => {
    setCreating(true);
    setError(null);
    try {
      const name = newName.trim();
      const res = await fetch("/instances", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, device_id: newDevice.trim() }),
      });
      if (!res.ok) throw new Error(await detail(res));
      setAdding(false);
      setNewName("");
      setNewDevice("");
      await reload();
      void launch(name);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setCreating(false);
    }
  }, [newName, newDevice, reload, launch]);

  if (!instance) return null;

  const nameValid = NAME_PATTERN.test(newName.trim());
  const nameTaken = rows.some((row) => row.declared && row.name.toLowerCase() === newName.trim().toLowerCase());
  const deviceTaken =
    devices?.find((device) => device.serial === newDevice.trim())?.used_by ?? [];
  const addButton = (
    <button
      type="button"
      onClick={() => {
        setError(null);
        setAdding(true);
      }}
      className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md border border-dashed
                 border-border text-sm text-muted-foreground hover:text-foreground
                 hover:border-primary/40 transition-colors
                 focus-visible:outline-2 focus-visible:outline-primary"
      title="Add an instance for another emulator"
    >
      <Plus className="w-4 h-4" />
      {others.length === 0 && !instance.declared && <span>Add instance</span>}
    </button>
  );

  const dialog = (
    <Dialog open={adding} onOpenChange={setAdding}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add an instance</DialogTitle>
          <DialogDescription>
            One instance drives one emulator. It starts with a copy of this instance&apos;s
            settings, pointed at its own emulator, and gets its own tab, hotkey and logs.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <label className="grid gap-1 text-sm">
            <span>Name</span>
            <Input
              autoFocus
              value={newName}
              maxLength={24}
              placeholder="e.g. stamina"
              onChange={(e) => setNewName(e.target.value)}
            />
            {newName && !nameValid && (
              <span className="text-xs text-destructive">
                Letters, digits, dashes or underscores, starting with a letter or digit.
              </span>
            )}
            {nameTaken && <span className="text-xs text-destructive">That name is taken.</span>}
          </label>
          <div className="grid gap-1 text-sm">
            <span>Emulator</span>
            <div className="flex flex-wrap gap-1.5">
              {devices === null && (
                <span className="text-xs text-muted-foreground">Asking adb…</span>
              )}
              {devices?.length === 0 && (
                <span className="text-xs text-muted-foreground">
                  adb sees no emulators. Start one, then look for it.
                </span>
              )}
              {devices?.map((device) => {
                const taken = device.used_by.length > 0;
                const chosen = newDevice.trim() === device.serial;
                return (
                  <button
                    key={device.serial}
                    type="button"
                    onClick={() => {
                      setNewDevice(device.serial);
                      setProbe(null);
                    }}
                    className={`flex flex-col items-start px-2 py-1 rounded-md border text-left
                                text-xs transition-colors focus-visible:outline-2
                                focus-visible:outline-primary ${
                                  chosen
                                    ? "border-primary bg-primary/10"
                                    : "border-border hover:bg-accent"
                                }`}
                    title={
                      (device.same_as ? `The same emulator as ${device.same_as}, on another address. ` : "") +
                      (taken ? `Already set on ${device.used_by.join(", ")}.` : device.state)
                    }
                  >
                    <span className="font-medium tabular-nums">{device.serial}</span>
                    <span className={taken ? "text-destructive" : "text-muted-foreground"}>
                      {taken ? `used by ${device.used_by.join(", ")}` : device.state}
                      {device.same_as ? ` · same as ${device.same_as}` : ""}
                    </span>
                  </button>
                );
              })}
              <button
                type="button"
                disabled={scanning}
                onClick={() => void loadDevices(true)}
                className="px-2 py-1 rounded-md border border-dashed border-border text-xs
                           text-muted-foreground hover:text-foreground hover:border-primary/40
                           disabled:opacity-60 focus-visible:outline-2 focus-visible:outline-primary"
                title="Try the ports MuMu, LDPlayer, BlueStacks, Nox and MEmu use"
              >
                {scanning ? "Looking…" : "Look for emulators"}
              </button>
            </div>
            <div className="flex gap-2 items-center">
              <Input
                aria-label="Emulator ADB address"
                value={newDevice}
                placeholder="or type it, e.g. 127.0.0.1:5565"
                onChange={(e) => {
                  setNewDevice(e.target.value);
                  setProbe(null);
                }}
              />
              <Button
                type="button"
                variant="outline"
                disabled={!newDevice.trim() || probing}
                onClick={() => void testDevice()}
              >
                {probing ? "Testing…" : "Test"}
              </Button>
            </div>
            {probe && (
              <span
                className={`text-xs ${probe.status === "success" ? "text-primary" : "text-destructive"}`}
              >
                {probe.detail}
              </span>
            )}
            {deviceTaken.length > 0 && (
              <span className="text-xs text-destructive">
                {deviceTaken.join(", ")} {deviceTaken.length === 1 ? "is" : "are"} already set to
                this emulator. Two bots on one emulator fight over the screen, so the second
                one will refuse to start.
              </span>
            )}
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => setAdding(false)}>
            Cancel
          </Button>
          <Button
            type="button"
            disabled={!nameValid || nameTaken || !newDevice.trim() || deviceTaken.length > 0 || creating}
            onClick={() => void create()}
          >
            Add and start
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );

  // A single, unnamed setup sees only the way to add a second instance; the tab strip
  // appears once there is something to switch between.
  if (!instance.declared && others.length === 0) {
    return (
      <div className="flex items-center gap-1.5">
        {addButton}
        {dialog}
      </div>
    );
  }

  // The version the process serving this page started on (the boot stamp). Shown on the
  // current tab so a stale instance -- one that updated in place but never restarted --
  // keeps advertising the build it actually runs instead of the number version.txt moved
  // to. Sibling tabs show no version: each is served by its own process, and only that
  // process knows what it stamped at startup.
  const details = [
    instance.device_id ?? (instance.use_adb ? "no device" : "desktop"),
    instance.hotkey?.toUpperCase(),
    instance.port ? `:${instance.port}` : null,
    instance.version ? `v${instance.version}` : null,
  ].filter(Boolean);

  const current = rows.find((row) => row.current) ?? { ...instance, current: true, running: true };
  const ordered = rows.length > 0 ? rows : [current];

  return (
    <div className="flex flex-col gap-1">
      <div role="tablist" aria-label="Instances" className="flex items-center gap-1.5 flex-wrap">
        {ordered.map((row) => {
          const state = busy[row.name];
          if (row.current) {
            return (
              <div
                key={`current-${row.name}`}
                role="tab"
                aria-selected="true"
                className="flex items-center gap-2 px-3 py-1.5 rounded-md border border-primary/40
                           bg-primary/10 text-sm"
                title={`Settings here are saved to ${instance.config_file}`}
              >
                <MonitorSmartphone className="w-4 h-4 text-primary shrink-0" />
                <span className="font-semibold text-primary">{tabLabel(instance)}</span>
                <span className="text-muted-foreground tabular-nums">{details.join(" · ")}</span>
                {presetLabel(row) && (
                  <span className="text-xs text-muted-foreground border-l border-border pl-2">
                    {presetLabel(row)}
                  </span>
                )}
                {instance.device_from_command_line && (
                  <span className="text-xs text-muted-foreground">(device from --use-adb)</span>
                )}
              </div>
            );
          }
          if (row.running && row.port) {
            return (
              <div key={`port-${row.port}`} className="flex items-stretch">
                <button
                  type="button"
                  role="tab"
                  aria-selected="false"
                  disabled={switching !== null || state !== undefined}
                  onClick={() => void switchTo(row.port as number)}
                  className={`flex items-center gap-2 px-3 py-1.5 border border-border bg-card/60
                              text-sm hover:bg-accent hover:border-primary/40 transition-colors
                              focus-visible:outline-2 focus-visible:outline-primary
                              disabled:opacity-60 ${row.declared ? "rounded-l-md" : "rounded-md"}`}
                  title={`Switch to ${tabLabel(row)} -- ${row.device_id ?? "desktop"} on :${row.port}` +
                    (presetLabel(row) ? `, running ${presetLabel(row)}` : "")}
                >
                  <span className="w-1.5 h-1.5 rounded-full bg-primary shrink-0" aria-hidden />
                  <span className="font-medium">{tabLabel(row)}</span>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {state === "stopping" ? "stopping…" : row.hotkey?.toUpperCase()}
                  </span>
                </button>
                {row.declared && (
                  <button
                    type="button"
                    aria-label={`Close ${row.name}`}
                    title={`Close ${row.name}`}
                    disabled={state !== undefined}
                    onClick={() => void stop(row)}
                    className="px-2 rounded-r-md border border-l-0 border-border bg-card/60
                               text-muted-foreground hover:text-destructive hover:bg-accent
                               focus-visible:outline-2 focus-visible:outline-primary
                               disabled:opacity-60 transition-colors"
                  >
                    <Square className="w-3 h-3" />
                  </button>
                )}
              </div>
            );
          }
          return (
            <div
              key={`stopped-${row.name}`}
              role="tab"
              aria-selected="false"
              aria-disabled="true"
              className="flex items-center gap-2 pl-3 pr-1 py-1 rounded-md border border-dashed
                         border-border text-sm text-muted-foreground"
            >
              <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/50 shrink-0" aria-hidden />
              <span>{tabLabel(row)}</span>
              {state === "starting" ? (
                <span className="text-xs px-1.5">starting…</span>
              ) : (
                <button
                  type="button"
                  onClick={() => void launch(row.name)}
                  className="flex items-center gap-1 text-xs px-1.5 py-0.5 rounded
                             hover:bg-accent hover:text-foreground
                             focus-visible:outline-2 focus-visible:outline-primary"
                  title={`Start ${row.name}`}
                >
                  <Play className="w-3 h-3" />
                  Start
                </button>
              )}
            </div>
          );
        })}
        {addButton}
        <button
          type="button"
          onClick={() => {
            setLogName(logKey(instance));
            setLogKind("log");
            setLogsOpen(true);
          }}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md border border-border
                     text-sm text-muted-foreground hover:text-foreground hover:border-primary/40
                     transition-colors focus-visible:outline-2 focus-visible:outline-primary"
          title="Read an instance's log"
        >
          <FileText className="w-4 h-4" />
          Logs
        </button>
      </div>
      {error && !adding && (
        <p className="text-xs text-destructive whitespace-pre-wrap max-w-3xl">{error}</p>
      )}
      {dialog}
      <Dialog open={logsOpen} onOpenChange={setLogsOpen}>
        <DialogContent className="sm:max-w-4xl">
          <DialogHeader>
            <DialogTitle>Logs</DialogTitle>
            <DialogDescription>
              The end of an instance&apos;s log, following it while this is open. Console
              output is what a launched instance printed before its log started -- where a
              crash on start shows up.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-wrap items-center gap-2">
            <select
              aria-label="Instance"
              className="border-1 border-border rounded-md px-2 py-1 bg-transparent dark:bg-input/30 text-sm"
              value={logName}
              onChange={(e) => setLogName(e.target.value)}
            >
              {ordered.map((row) => (
                <option key={logKey(row)} value={logKey(row)}>
                  {tabLabel(row)}
                  {row.running ? "" : " (not running)"}
                </option>
              ))}
            </select>
            <select
              aria-label="Which log"
              className="border-1 border-border rounded-md px-2 py-1 bg-transparent dark:bg-input/30 text-sm"
              value={logKind}
              onChange={(e) => setLogKind(e.target.value as "log" | "console")}
            >
              <option value="log">Log</option>
              <option value="console" disabled={logName === "default"}>
                Console output
              </option>
            </select>
          </div>
          <pre
            className="max-h-[60vh] overflow-auto rounded-md border border-border bg-muted/30 p-3
                       text-xs leading-relaxed whitespace-pre-wrap break-words tabular-nums"
          >
            {logText === null ? "No log yet." : logText}
          </pre>
        </DialogContent>
      </Dialog>
    </div>
  );
}
