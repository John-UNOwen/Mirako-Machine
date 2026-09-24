import { Cog } from "lucide-react";
import type { Config, UpdateConfigType } from "@/types";
import { Input } from "../ui/input";
import { Button } from "../ui/button";
import { Checkbox } from "../ui/checkbox";
import Tooltips from "@/components/_c/Tooltips";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../ui/select";
import { useCallback, useEffect, useState } from "react";
import { fetchUpdateStatus } from "../UpdateBanner";

// One line of `adb devices`, as GET /adb/devices returns it (server/main.py).
type DeviceRow = { serial: string; state: string; used_by: string[]; same_as?: string | null };

type Props = {
  config: Config;
  updateConfig: UpdateConfigType;
};

// The reason a failed response carries, so a 503 from a dead adb says why
// instead of reading as an empty list. Mirrors the helper in InstanceBanner.
async function detail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return body?.detail ?? `HTTP ${res.status}`;
  } catch {
    return `HTTP ${res.status}`;
  }
}

export default function SetUpSection({ config, updateConfig }: Props) {
  const {
    use_adb,
    device_id,
    window_name,
    ocr_use_gpu,
    auto_check_updates,
    notifications_enabled,
    error_notification,
    success_notification,
    notification_volume
  } = config;
  const [notificationSounds, setNotificationSounds] = useState<string[]>([]);
  const [adbTestResult, setAdbTestResult] = useState<{ status: string; detail: string } | null>(null);
  const [adbTesting, setAdbTesting] = useState(false);
  const [adbDevices, setAdbDevices] = useState<DeviceRow[] | null>(null);
  const [adbDevicesError, setAdbDevicesError] = useState<string | null>(null);
  const [adbScanning, setAdbScanning] = useState(false);
  const [usingDevice, setUsingDevice] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState<string>("");

  useEffect(() => {
    fetch("/notifs")
      .then((res) => res.json())
      .then((data) => {
        if (Array.isArray(data)) {
          setNotificationSounds(data);
        }
      })
      .catch((err) => console.error("Failed to fetch notification sounds", err));
  }, []);

  // What adb sees, asked for once on mount and again only when the scan button is
  // pressed: no polling, the list is as fresh as the last look. A failed ask leaves
  // the list empty and says the server's reason out -- a 503 'adb is not answering'
  // is adb itself being broken, which reads nothing like adb seeing no emulators --
  // rather than hanging on "Asking adb…". This is this instance's own page, so it
  // asks with exclude_self: a device it is itself using must not arrive as "used by
  // itself". The add-instance dialog (InstanceBanner) asks without it -- there this
  // instance's device is a real conflict for the instance being added.
  const loadDevices = useCallback(async (scan: boolean) => {
    if (scan) setAdbScanning(true);
    try {
      const res = await fetch(`/adb/devices?exclude_self=true${scan ? "&scan=true" : ""}`, { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        setAdbDevices(data.devices ?? []);
        setAdbDevicesError(null);
      } else {
        setAdbDevices([]);
        setAdbDevicesError(await detail(res));
      }
    } catch {
      setAdbDevices([]);
      setAdbDevicesError("Could not reach the server to ask adb.");
    } finally {
      setAdbScanning(false);
    }
  }, []);

  // Asked only while ADB is on: /adb/devices talks to adb, and asking with the setting
  // off would start an adb server -- or answer 503 -- on machines that never use it.
  // Toggling the setting on asks right away, so the list is there as it appears.
  useEffect(() => {
    if (!use_adb) return;
    void loadDevices(false);
  }, [use_adb, loadDevices]);

  // Asks now, whatever the setting says and whatever the cache holds: a button
  // labelled 'Check now' that answered from a six-hour-old cache would be lying.
  const checkNow = async () => {
    setChecking(true);
    setCheckResult("");
    const found = await fetchUpdateStatus(true);
    setChecking(false);
    if (!found) {
      setCheckResult("Could not reach the bot's server.");
      return;
    }
    if (found.error) {
      setCheckResult(found.error);
    } else if (found.behind) {
      setCheckResult(`Version ${found.latest} is out.`);
    } else {
      setCheckResult(`Up to date (${found.current}).`);
    }
  };

  const testAdbConnection = async () => {
    setAdbTesting(true);
    setAdbTestResult(null);
    try {
      const res = await fetch("/adb/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_id }),
      });
      const data = await res.json();
      setAdbTestResult(data);
    } catch {
      setAdbTestResult({ status: "fail", detail: "Could not reach the server to test ADB." });
    } finally {
      setAdbTesting(false);
    }
  };

  // Point the Device ID at a listed emulator, then run the same test flow on it. The
  // test goes out with the serial, not with `device_id`: that state only picks the new
  // value up on the next render, and a test on the old address would be a lie. The
  // result lands in the same detail line a Test button leaves, and a fully green test
  // persists the address on the server.
  const selectDevice = async (serial: string) => {
    setUsingDevice(serial);
    setAdbTestResult(null);
    updateConfig("device_id", serial);
    try {
      const res = await fetch("/adb/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_id: serial }),
      });
      const data = await res.json();
      setAdbTestResult(data);
    } catch {
      setAdbTestResult({ status: "fail", detail: "Could not reach the server to test ADB." });
    } finally {
      setUsingDevice(null);
    }
  };

  // One action at a time: while a Test or a Use is in flight, the Test button and
  // every Use button are off, so two cannot re-point the process at once. The scan
  // button is off too: its port burst and its letting go of newly found aliases can
  // disconnect the very address an in-flight test just connected -- a red test that
  // only the scan caused.
  const busy = adbTesting || usingDevice !== null;

  return (
    <div className="section-card">
      <h2 className="text-3xl font-semibold mb-6 flex items-center gap-3">
        <Cog className="text-primary" />
        Setup
      </h2>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-2">
        <label className="uma-label">
          <Checkbox
            checked={use_adb}
            onCheckedChange={() => updateConfig("use_adb", !use_adb)}
          />
          <span className="font-base">Use ADB</span>
          <Tooltips>If enabled bot will use ADB to connect to your emulator.
            You need to find the device ID which is the IP address of your emulator and its ADB port and enter it correctly as IP:Port.</Tooltips>
        </label>
        <label className={`flex flex-col items-start gap-1 cursor-pointer ${use_adb ? "" : "disabled"}`}>
          <div className="flex flex-row gap-2 w-fit items-center">
            <span className="font-base">Device ID</span>
            <Tooltips>
              {"ADB connection address in 'IP:Port' format (default: 127.0.0.1:5555).\n\
              Common ports:\n\
              - LDPlayer: 127.0.0.1:5555\n\
              - MuMu Player 12: 127.0.0.1:16384\n\
              - NoxPlayer: 127.0.0.1:62001\n\
              - BlueStacks 5: Check Settings -> Advanced -> Android Debug Bridge"}
            </Tooltips>
            <div className="flex items-center gap-2">
              <Input
                type="text"
                className="w-48"
                value={device_id}
                onChange={(e) => { updateConfig("device_id", e.target.value); setAdbTestResult(null); }}
              />
              <Button
                variant="outline"
                size="sm"
                disabled={busy || !device_id}
                onClick={testAdbConnection}
              >
                {adbTesting ? "Testing..." : "Test"}
              </Button>
            </div>
          </div>
          {use_adb && (
            <div className="flex flex-wrap gap-1.5">
              {adbDevices === null && !adbDevicesError && (
                <span className="text-xs text-muted-foreground">Asking adb…</span>
              )}
              {adbDevicesError && (
                <span className="text-xs text-red-500">{adbDevicesError}</span>
              )}
              {!adbDevicesError && adbDevices?.length === 0 && (
                <span className="text-xs text-muted-foreground">
                  adb sees no emulators. Start one, then look for it.
                </span>
              )}
              {adbDevices?.map((device) => {
                const taken = device.used_by.length > 0;
                const chosen = device_id.trim() === device.serial;
                return (
                  <div
                    key={device.serial}
                    title={
                      (device.same_as ? `The same emulator as ${device.same_as}, on another address. ` : "") +
                      (taken ? `Already set on ${device.used_by.join(", ")}.` : device.state)
                    }
                    className={`flex items-center gap-1.5 px-2 py-1 rounded-md border text-left
                                text-xs transition-colors ${
                      chosen ? "border-primary bg-primary/10" : "border-border hover:bg-accent"
                    }`}
                  >
                    <span className="font-medium tabular-nums">{device.serial}</span>
                    <span className={taken ? "text-destructive" : "text-muted-foreground"}>
                      {taken ? `used by ${device.used_by.join(", ")}` : device.state}
                      {device.same_as ? ` · same as ${device.same_as}` : ""}
                    </span>
                    {device.state === "device" && (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="h-6 px-2 text-xs shadow-none"
                        disabled={busy}
                        onClick={() => void selectDevice(device.serial)}
                      >
                        {usingDevice === device.serial ? "Using…" : "Use"}
                      </Button>
                    )}
                  </div>
                );
              })}
              <button
                type="button"
                disabled={busy || adbScanning}
                onClick={() => void loadDevices(true)}
                className="px-2 py-1 rounded-md border border-dashed border-border text-xs
                           text-muted-foreground hover:text-foreground hover:border-primary/40
                           disabled:opacity-60 focus-visible:outline-2 focus-visible:outline-primary"
                title="Try the ports MuMu, LDPlayer, BlueStacks, Nox and MEmu use"
              >
                {adbScanning ? "Looking…" : "Look on common emulator ports"}
              </button>
            </div>
          )}
          {adbTestResult && (
            <span className={`text-xs mt-1 ${adbTestResult.status === "success" ? "text-green-500" : "text-red-500"}`}>
              {adbTestResult.detail}
            </span>
          )}
        </label>
        <label className={`uma-label ${use_adb ? "disabled" : ""}`}>
          <span className="font-base">Window Name</span>
          <Tooltips>
            {"If you're using an emulator but NOT ADB, set this to your emulator's window title, exactly (case-sensitive).\n\
            main.py refuses to start without it on that path, and emulators rename their window per instance."}
          </Tooltips>
          <Input
            className="w-48"
            value={window_name}
            onChange={(e) => updateConfig("window_name", e.target.value)}
          />
        </label>
        <label className="uma-label">
          <Checkbox
            checked={ocr_use_gpu}
            onCheckedChange={() => updateConfig("ocr_use_gpu", !ocr_use_gpu)}
          />
          <span className="font-base">Use GPU for OCR</span>
          <Tooltips>Runs the OCR models on the GPU through DirectML when the machine offers it. Measured at a few percent over the CPU, so leaving it off costs little; a GPU build that fails to start falls back to the CPU on its own.</Tooltips>
        </label>
        <label className="uma-label">
          <Checkbox
            checked={auto_check_updates}
            onCheckedChange={() => updateConfig("auto_check_updates", !auto_check_updates)}
          />
          <span className="font-base">Check for updates</span>
          <Tooltips>
            {"Asks GitHub at startup whether a newer version has been published, at most once every six hours, and says so in the banner at the top.\n\
            This switch only looks: nothing is downloaded and nothing in the bot folder is changed. Installing what the banner finds is a button there -- the bot pulls the new version itself when this copy is a git checkout, and a copy downloaded as a zip updates by unpacking the latest zip over it."}
          </Tooltips>
          <div className="flex items-center gap-2 mt-1">
            <Button variant="outline" size="sm" disabled={checking} onClick={checkNow}>
              {checking ? "Checking..." : "Check now"}
            </Button>
            {checkResult && <span className="text-xs text-muted-foreground">{checkResult}</span>}
          </div>
        </label>
        <label className="col-span-3 uma-label">
          <Checkbox checked={notifications_enabled} onCheckedChange={() => updateConfig("notifications_enabled", !notifications_enabled)} />
          <span className="font-base">Enable Notification Sounds</span><Tooltips>Enables sounds to play as notifications. You can use custom sounds by adding them to assets/notifications folder of the bot.</Tooltips>
        </label>
        <label className={`uma-label ${notifications_enabled ? "" : "disabled"}`}>
          <div className="flex gap-2 items-center">
            <span className="font-base">
              Error Sound
            </span><Tooltips>Plays when the bot gets stuck.</Tooltips>
          </div>
          <Select value={error_notification} onValueChange={(v) => updateConfig("error_notification", v)}>
            <SelectTrigger className="w-48">
              <SelectValue placeholder="Select sound" />
            </SelectTrigger>
            <SelectContent>
              {notificationSounds.map((sound) => (
                <SelectItem key={sound} value={sound}>
                  {sound}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <label className={`uma-label ${notifications_enabled ? "" : "disabled"}`}>
          <div className="flex gap-2 items-center">
            <span className="font-base">
              Success Sound
            </span><Tooltips>Plays when the bot has finished a run.</Tooltips>
          </div>
          <Select value={success_notification} onValueChange={(v) => updateConfig("success_notification", v)}>
            <SelectTrigger className="w-48">
              <SelectValue placeholder="Select sound" />
            </SelectTrigger>
            <SelectContent>
              {notificationSounds.map((sound) => (
                <SelectItem key={sound} value={sound}>
                  {sound}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <label className={`uma-label ${notifications_enabled ? "" : "disabled"} col-span-3`}>
          <div className="flex items-center gap-4">
            <span className="font-base min-w-[160px]">
              Notification Volume
            </span>

            <input
              type="range"
              min={0}
              max={1}
              step="any"
              value={notification_volume}
              onChange={(e) =>
                updateConfig(
                  "notification_volume",
                  Math.round(parseFloat(e.target.value) * 100) / 100
                )
              }
              className="w-64 accent-primary"
            />

            <span className="w-14 text-right tabular-nums">
              {Math.round(notification_volume * 100)}%
            </span>
          </div>
        </label>
      </div>
    </div>
  );
}
