import { useEffect, useState, useCallback, useMemo, useRef } from "react";

import rawConfig from "../../config.template.json";
import { useConfigPreset } from "./hooks/useConfigPreset";
import { useConfig } from "./hooks/useConfig";
import { useImportConfig } from "./hooks/useImportConfig";
import { Pencil, CheckCircle2, AlertCircle, AlertTriangle, Sun, Moon, Plus, Copy, Trash2, ChevronDown, FolderUp, FolderDown, Settings2, Check, Loader2 } from "lucide-react";

import type { Config } from "./types";
import { SETUP_KEYS, type SetupConfig as SharedSetupConfig } from "./constants/setupKeys";

import { Button } from "./components/ui/button";
import { Input } from "./components/ui/input";
import { Sidebar } from "./components/ui/Sidebar";
import BotControl from "./components/BotControl";
import InstanceBanner, { useInstance, useInstances, usersOf } from "./components/InstanceBanner";
import UpdateBanner, { useUpdateStatus } from "./components/UpdateBanner";
import OverviewSection from "./components/OverviewSection";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./components/ui/select";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "./components/ui/dialog";

import SetUpSection from "./components/set-up/SetUpSection";
import SkillListSection from "./components/skill/SkillListSection";
import IndependentSection from "./components/independent/IndependentSection";
import DebugSection from "./components/independent/DebugSection";
import StatisticsSection from "./components/independent/StatisticsSection";
import Tooltips from "@/components/_c/Tooltips";

interface Theme {
  id: string;
  label: string;
  primary: string;
  secondary: string;
  dark: boolean;
}

// The preset picker -- the "Configuration File" dropdown, its rename pencil and the
// Manage menu. Setting this to false hides the whole control and leaves everything behind
// it wired up: import, export, duplicate, delete, and the switch itself. Config files go
// on working either way, since the bot reads the active one from disk and never asks the
// UI which it is.
const SHOW_PRESET_PICKER: boolean = true;


// One list, in constants/setupKeys.ts. This file kept a second copy: they agreed, but a
// future edit to one would have silently forked "which keys are stripped on import" from
// "which keys are split out on save".
type SetupConfig = SharedSetupConfig;

const pickSetupConfig = (config: Config): SetupConfig => ({
  sleep_time_multiplier: config.sleep_time_multiplier,
  use_adb: config.use_adb,
  window_name: config.window_name,
  device_id: config.device_id,
  ocr_use_gpu: config.ocr_use_gpu,
  auto_check_updates: config.auto_check_updates,
  notifications_enabled: config.notifications_enabled,
  error_notification: config.error_notification,
  success_notification: config.success_notification,
  notification_volume: config.notification_volume,
  preset_id: config.preset_id,
});

const stripSetupConfig = (config: Config): Config => {
  const next = { ...config } as Partial<Config>;
  for (const key of SETUP_KEYS) {
    delete next[key];
  }
  return next as Config;
};

const mergeConfigWithSetup = (config: Config, setup: SetupConfig): Config => ({
  ...stripSetupConfig(config),
  ...setup,
});

// Key order is not content. Two configs with the same settings in a different order are the
// same config, and comparing them as plain JSON said otherwise: a key the server sends that
// the page lists elsewhere put "Saving changes" up after every edit and kept it there, with
// nothing to save. Objects are compared with their keys sorted; arrays keep their order,
// which is content -- the skill list's order is its priority.
const canonical = (value: unknown): string =>
  JSON.stringify(value, (_key, inner) =>
    inner && typeof inner === "object" && !Array.isArray(inner)
      ? Object.fromEntries(Object.entries(inner).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
      : inner
  );

const sanitizeFileName = (value: string): string => {
  const sanitized = Array.from(value, (char) => {
    const code = char.charCodeAt(0);
    if (code <= 31) return "_";
    return '<>:"/\\|?*'.includes(char) ? "_" : char;
  }).join("").trim();
  return sanitized || "config";
};

function App() {
  const [appVersion, setAppVersion] = useState<string>("");
  const [themes, setThemes] = useState<Theme[]>([]);
  // Arriving from another instance's tab carries the section and the light/dark choice
  // in the query, because each port keeps its own storage. Read once, then dropped from
  // the address bar so a reload does not keep re-applying it.
  const [arrival] = useState(() => {
    const query = new URLSearchParams(window.location.search);
    const found = { tab: query.get("tab"), theme: query.get("theme") };
    if (found.tab || found.theme) {
      window.history.replaceState(null, "", window.location.pathname);
    }
    return found;
  });
  const [activeTab, setActiveTab] = useState<string>(() => arrival.tab || "independent");
  const [isEditing, setIsEditing] = useState(false);
  const [isPresetActionsOpen, setIsPresetActionsOpen] = useState(false);
  const [isDiscardDialogOpen, setIsDiscardDialogOpen] = useState(false);
  const [pendingConfigSwitchId, setPendingConfigSwitchId] = useState<string | null>(null);
  const presetActionsRef = useRef<HTMLDivElement>(null);
  const [isDark, setIsDark] = useState(() => {
    if (arrival.theme === "dark" || arrival.theme === "light") {
      return arrival.theme === "dark";
    }
    if (typeof window !== "undefined") {
      return (
        localStorage.theme === "dark" ||
        (!("theme" in localStorage) && window.matchMedia("(prefers-color-scheme: dark)").matches)
      );
    }
    return false;
  });
  useEffect(() => {
    if (isDark) {
      document.documentElement.classList.add("dark");
      localStorage.theme = "dark";
    } else {
      document.documentElement.classList.remove("dark");
      localStorage.theme = "light";
    }
  }, [isDark]);

  // /version.txt is the boot-stamped version this process started on, not a re-read of
  // version.txt per request -- so after an in-place update the sidebar keeps saying what
  // is actually running until the process is restarted. The banner beside it does the same.
  useEffect(() => {
    fetch("/version.txt")
      .then(r => {
        if (!r.ok) throw new Error("version fetch failed")
        return r.text()
      })
      .then(v => setAppVersion(v.trim()))
      .catch(() => setAppVersion("unknown"))
  }, []);

  const defaultConfig = rawConfig as Config;
  const instance = useInstance();
  const updateStatus = useUpdateStatus();
  const { rows: instances, reload: reloadInstances } = useInstances();
  const [setupConfig, setSetupConfig] = useState<SetupConfig>(() =>
    pickSetupConfig(defaultConfig)
  );
  const {
    activeIndex,
    activeConfig,
    activeConfigId,
    presets,
    setActiveIndex,
    savePresetById,
    savePreset,
    createPreset,
    duplicatePreset,
    deletePreset,
    appliedPresetId,
    setAppliedPresetId,
    holdReloadRef,
    activeConfigLoads,
    acceptSaved,
    reloadActiveConfig,
    presetsReady,
  } = useConfigPreset();
  const { config, setConfig, saveConfig, toast, notify } = useConfig(activeConfig?.config ?? defaultConfig);
  const { fileInputRef, openFileDialog, handleImport } = useImportConfig({
    activeConfig: config,
    createPreset,
    savePresetById,
    reloadActiveConfig,
  });

  // Bumped when the Setup values arrive from the server -- the one time they are loaded
  // rather than saved. See the workspace rebuild below.
  const [setupLoads, setSetupLoads] = useState(0);
  useEffect(() => {
    const getSetupConfig = async () => {
      try {
        const res = await fetch("/config/setup");
        if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
        const data = await res.json();
        setSetupConfig((prev) => ({ ...prev, ...data }));
        setSetupLoads((n) => n + 1);
      } catch (error) {
        console.error("Failed to load setup config:", error);
      }
    };
    getSetupConfig();
  }, []);

  // Rebuild the workspace from what was loaded -- and only when something was loaded: a
  // preset fetched, switched to, or cleared, or the Setup values arriving. This used to
  // run whenever `presets`, `activeConfig` or `setupConfig` changed, and a save changes
  // all three. Rebuilt from a preset copy that had not seen the save, the page threw the
  // edit away a second after making it, while the server kept it: remove a skill and it
  // came back, remove it again and it came back again. Saves now update the baseline
  // through acceptSaved and leave the workspace alone, so an edit made while a save was
  // in flight survives it too.
  useEffect(() => {
    if (presets[activeIndex]) {
      setConfig(mergeConfigWithSetup(activeConfig?.config ?? defaultConfig, setupConfig));
    } else {
      setConfig(mergeConfigWithSetup(defaultConfig, setupConfig));
    }
    // Deliberately keyed on the load counters, not on the values it reads: those change
    // on every save as well, which is exactly what must not rebuild the page.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeConfigLoads, setupLoads, defaultConfig, setConfig]);

  const baselineConfig = useMemo(
    () => mergeConfigWithSetup(activeConfig?.config ?? defaultConfig, setupConfig),
    [activeIndex, activeConfig, defaultConfig, presets, setupConfig]
  );
  const isDirty = useMemo(
    () => canonical(config) !== canonical(baselineConfig),
    [baselineConfig, config]
  );

  // The theme shown, which is not always the one saved: a preset naming a theme this
  // install does not have (a custom one deleted by an update, a preset from another
  // machine) is shown in the first theme and left as it is. It used to be rewritten to
  // the first theme, which marked the page dirty and auto-saved the user's choice away on
  // every load.
  const effectiveThemeId =
    (themes.find((t) => t.id === config.theme) ?? themes[0])?.id ?? config.theme ?? "";
  useEffect(() => {
    fetch("/themes")
      .then((res) => res.json())
      .then((data) => setThemes(data))
      .catch((err) => console.error("Failed to load themes:", err));
  }, []);

  const updateConfig = useCallback(<K extends keyof typeof config>(key: K, value: (typeof config)[K]) => {
    setConfig((prev) => ({ ...prev, [key]: value }));
  }, [setConfig]);

  const exportCurrentConfig = useCallback(() => {
    const fileNameBase = sanitizeFileName(config.config_name || activeConfigId || "config");
    const blob = new Blob([JSON.stringify(stripSetupConfig(config), null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${fileNameBase}.json`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }, [config, activeConfigId]);

  const switchToPresetById = useCallback((presetId: string) => {
    const idx = presets.findIndex((preset) => preset.id === presetId);
    if (idx < 0) return;
    setActiveIndex(idx);
    setIsEditing(false);
  }, [presets, setActiveIndex]);

  const requestPresetSwitch = useCallback(async (presetId: string) => {
    if (presetId === activeConfigId) return;
    if (!isDirty) {
      switchToPresetById(presetId);
      return;
    }
    // Edits are saved automatically, so a switch during the debounce window has nothing
    // to discard -- it just has to happen after the pending write. The dialog survives
    // for the one case where that still loses work: a save that failed.
    if (await autoSaveRef.current()) {
      switchToPresetById(presetId);
      return;
    }
    setPendingConfigSwitchId(presetId);
    setIsDiscardDialogOpen(true);
  }, [activeConfigId, isDirty, switchToPresetById]);

  const persistPresetAndSetup = useCallback(async (): Promise<Config> => {
    // Only the *applied* preset owns this pointer. Editing a preset the user has not
    // applied used to repoint the instance at it -- on the next load the UI called it
    // applied, auto-save started writing its values through to the config the bot reads,
    // and the instance silently changed which preset it was running.
    if (activeConfigId === appliedPresetId) {
      config.preset_id = activeConfigId;
    }
    const nextSetup = pickSetupConfig(config);
    const configWithoutSetup = stripSetupConfig(config);

    const mergedConfig = mergeConfigWithSetup(configWithoutSetup, nextSetup);
    await savePreset(configWithoutSetup);
    // What the server now holds for this preset, so what the page is compared against.
    // Without it the baseline stayed at the pre-edit copy, and the page could not tell a
    // saved edit from an unsaved one.
    acceptSaved(activeConfigId, configWithoutSetup);
    const setupRes = await fetch("/config/setup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(nextSetup),
    });
    if (!setupRes.ok) {
      throw new Error(`Failed to save setup config. HTTP status: ${setupRes.status}`);
    }
    setSetupConfig(nextSetup);
    return mergedConfig;
  }, [config, savePreset, activeConfigId, appliedPresetId, acceptSaved]);

  // Auto-save. Long enough that typing a webhook URL is one save rather than forty,
  // short enough that hitting F1 straight after a change picks it up.
  const AUTO_SAVE_DELAY_MS = 900;
  // How long "Saved" stays up, and how long a failed save waits before trying again.
  const SAVED_SHOWN_MS = 2000;
  const RETRY_FAILED_SAVE_MS = 5000;
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "failed">("idle");
  // Bumped on every failed save, so each failure schedules the next retry. Without it a
  // retry that failed quickly went failed -> saving -> failed inside one batch, which
  // React applies as no change at all: the second failure scheduled nothing, and the
  // page sat on "Not saved" for good. Found by making the saves fail in a live page.
  const [saveFailures, setSaveFailures] = useState(0);
  // Nothing is saved until the page holds what the server holds. The page renders from
  // the template before either load lands, and an edit in that window auto-saved the
  // template's Setup values -- device, window, sounds -- over the instance's real ones.
  // An edit made then is replaced by the loaded values anyway, so there is nothing of the
  // user's to keep: the save is simply not made.
  const loaded =
    presetsReady &&
    setupLoads > 0 &&
    (activeConfigId === "" || activeConfig?.id === activeConfigId);
  // The exact config last written. Guards against a save loop: if anything about the
  // round trip left isDirty true, this stops the effect writing the same bytes forever.
  const lastSavedRef = useRef<string>("");
  // requestPresetSwitch is declared before autoSave and must not be rebuilt on every
  // config keystroke, so it reaches the current one through a ref.
  const autoSaveRef = useRef<() => Promise<boolean>>(async () => true);

  const autoSave = useCallback(async (): Promise<boolean> => {
    if (!loaded) return true;
    const snapshot = canonical(config);
    if (snapshot === lastSavedRef.current) return true;
    setSaveState("saving");
    try {
      const mergedConfig = await persistPresetAndSetup();
      // The preset is not what the bot reads -- config.json is. Writing only the preset
      // would leave a setting that looks saved and changes nothing, so the applied
      // preset writes through to both. A preset being edited while a different one is
      // applied deliberately does not.
      if (activeConfigId && activeConfigId === appliedPresetId) {
        await saveConfig(mergedConfig, true);
      }
      lastSavedRef.current = snapshot;
      setSaveState("saved");
      return true;
    } catch (error) {
      // Said on the page, not only in the console. It went back to idle here, which with
      // the edit still unsaved drew the "Saving changes" spinner -- indefinitely, for a
      // save that had already failed.
      console.error("Auto-save failed:", error);
      setSaveState("failed");
      setSaveFailures((n) => n + 1);
      return false;
    }
  }, [loaded, config, persistPresetAndSetup, saveConfig, activeConfigId, appliedPresetId]);

  useEffect(() => {
    autoSaveRef.current = autoSave;
  }, [autoSave]);

  // The focus re-read in useConfigPreset replaces the workspace with what is on disk.
  // That is right when this page has nothing pending and wrong when it does: an edit
  // still inside the auto-save debounce, or one whose save failed, is only held here.
  // Saving is also a hold, because the re-read would race the write it is about to do.
  useEffect(() => {
    holdReloadRef.current = () => isDirty || saveState === "saving";
  }, [holdReloadRef, isDirty, saveState]);

  // One save per pause in editing. autoSave changes only when the config does, so each
  // edit restarts the wait and nothing else does.
  useEffect(() => {
    if (!isDirty) {
      // Back to clean without saving (an edit undone): a failure has nothing left to say.
      setSaveState((current) => (current === "failed" ? "idle" : current));
      return;
    }
    // In flight: its completion re-runs this, and an edit made meanwhile is saved then.
    // Failed: the retry below owns it.
    if (saveState === "saving" || saveState === "failed") return;
    const timer = window.setTimeout(() => void autoSave(), AUTO_SAVE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [isDirty, autoSave, saveState]);

  // A failed save is tried again on its own. It used to be retried by accident: the effect
  // above re-ran after every render, so a failure went back round every 900ms under a
  // spinner that said it was saving. With that accident gone, the retry is deliberate,
  // slower, and the pill says the save failed.
  useEffect(() => {
    if (saveState !== "failed" || !isDirty) return;
    const timer = window.setTimeout(() => void autoSave(), RETRY_FAILED_SAVE_MS);
    return () => window.clearTimeout(timer);
  }, [saveState, saveFailures, isDirty, autoSave]);

  // "Saved" is shown for a moment and then goes. It used to be cleared in the same render
  // that set it, so the only state anyone ever saw was "Saving changes".
  useEffect(() => {
    if (saveState !== "saved") return;
    const timer = window.setTimeout(
      () => setSaveState((current) => (current === "saved" ? "idle" : current)),
      SAVED_SHOWN_MS,
    );
    return () => window.clearTimeout(timer);
  }, [saveState]);

  const handleApplyPreset = useCallback(async () => {
    try {
      const mergedConfig = await persistPresetAndSetup();
      await saveConfig(mergedConfig);
      if (activeConfigId) {
        await setAppliedPresetId(activeConfigId);
      }
      setIsEditing(false);
    } catch (error) {
      // Announced: this only reached the console, so a failed Apply left the user sure
      // the bot now ran the new preset when neither the file nor the pointer had changed.
      console.error("Failed to apply preset:", error);
      notify(`Could not apply the preset: ${error instanceof Error ? error.message : error}`, true);
    }
  }, [activeConfigId, persistPresetAndSetup, saveConfig, setAppliedPresetId, notify]);

  useEffect(() => {
    if (!isPresetActionsOpen) return;
    const handleClickOutside = (event: MouseEvent) => {
      if (!presetActionsRef.current?.contains(event.target as Node)) {
        setIsPresetActionsOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [isPresetActionsOpen]);

  useEffect(() => {
    const activeTheme = themes.find((t) => t.id === effectiveThemeId);
    if (activeTheme) {
      document.documentElement.style.setProperty("--primary", activeTheme.primary);
      document.documentElement.style.setProperty("--secondary", activeTheme.secondary);
    }
  }, [themes, effectiveThemeId]);


  // Waits for a config with the shape the page renders. This used to test
  // event.event_choices, a career-mode block; with that block retired the page would
  // have sat on Loading forever, so it tests the block the sidebar actually reads.
  if (!config?.skill?.skill_list) {
    return <div>Loading...</div>;
  }
  // Every debug switch that changes what a run does, with what it does to it. Listed
  // here rather than left to the Debug tab because that tab is the one place a user is
  // not looking when they start a run, and each of these quietly makes a career do
  // nothing -- the failure is silent and costs the whole run.
  const activeDebugFlags = [
    {
      on: config.independent_training.debug_stop_before_start,
      label: "careers stop before starting",
    },
    {
      on: config.independent_training.debug_select_skills_only,
      label: "skills selected but not bought",
    },
    {
      on: config.independent_training.debug_force_tp_refill,
      label: "TP refill never buys",
    },
    {
      on: config.independent_training.debug_pretend_tp_short,
      label: "next career held back once (TP wait test)",
    },
  ].filter((flag) => flag.on);

  const renderContent = () => {
    const props = { config, updateConfig };
    // Set-Up is not offered in the sidebar but is kept as the fallback: it edits the
    // window name, ADB, OCR device and notification sounds, none of which are career
    // settings and none of which have another UI.
    switch (activeTab) {
      case "overview": return <OverviewSection />;
      case "set-up": return <SetUpSection {...props} />;
      case "independent": return <IndependentSection {...props} />;
      case "skills": return <SkillListSection {...props} />;
      case "statistics": return <StatisticsSection />;
      case "debug": return <DebugSection {...props} />;
      default: return <SetUpSection {...props} />;
    }
  };

  return (
    <main className="flex min-h-screen w-full bg-triangles overflow-hidden">
      <Sidebar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        appVersion={updateStatus?.commit ? `${appVersion} (${updateStatus.commit})` : appVersion}
        skillCount={config.skill.skill_list.length}
      />

      <div className="flex-1 flex flex-col overflow-y-auto">
        <header className="p-6 w-full py-4 self-start border-b border-border flex flex-col gap-3 sticky top-0 z-100 backdrop-blur-md">

          {/* Toast Notification Layer */}
          {((loaded && isDirty) || saveState !== "idle") && (
            <div className="absolute top-3 left-1/2 -translate-x-1/2 flex items-center gap-2 px-4 py-2 rounded-full text-sm border bg-card/95 backdrop-blur-md shadow-md z-20 text-muted-foreground">
              {saveState === "failed" && isDirty ? (
                <>
                  <AlertCircle className="w-4 h-4 text-red-500" />
                  Not saved. Trying again shortly.
                </>
              ) : saveState === "saved" && !isDirty ? (
                <>
                  <Check className="w-4 h-4 text-primary" />
                  Saved
                </>
              ) : (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Saving changes
                </>
              )}
            </div>
          )}
          {toast.show && (
            <div className={`absolute top-14 left-1/2 -translate-x-1/2 flex items-center gap-2 px-4 py-1 rounded-full text-sm font-medium animate-in fade-in zoom-in duration-300 border ${toast.isError
              ? "bg-destructive/10 border-destructive/20 text-destructive"
              : "bg-primary/10 border-primary/20 text-primary"
              }`}>
              {toast.isError ? <AlertCircle size={14} /> : <CheckCircle2 size={14} />}
              {toast.message}
            </div>
          )}

          {/* The instance tabs get a strip of their own: which emulator the page is
              editing sits above everything the page edits. Renders nothing for a
              single, unnamed setup, which keeps its header as it was. */}
          {/* Says when a newer build is published. Nothing here pulls: the
              decision, and the timing, stay with the user. */}
          <UpdateBanner status={updateStatus} />

          <InstanceBanner
            instance={instance}
            rows={instances}
            reload={reloadInstances}
            beforeSwitch={() => autoSaveRef.current()}
            tab={activeTab}
            dark={isDark}
          />

          <div className="flex items-end justify-between w-full">
            <div className="flex items-center gap-4">
              {SHOW_PRESET_PICKER && (
              <div className="space-y-1 relative" ref={presetActionsRef}>
                <label className="text-xs font-thin text-muted-foreground ml-1 mr-2">Preset</label>
                <Tooltips size="xs">{"Presets are saved as files in the bot folder under config/, and any instance can run one.\n" +
                  "A preset is shared: saving it changes every instance that runs it. The Setup page (device, window) belongs to each instance and is never part of a preset."}</Tooltips>
                {usersOf(instances, activeConfigId).length > 0 && (
                  <span className="text-xs text-primary ml-2">
                    Also runs on {usersOf(instances, activeConfigId).join(", ")} -- changes here reach{" "}
                    {usersOf(instances, activeConfigId).length === 1 ? "it" : "them"} too
                  </span>
                )}
                <div className="flex items-stretch shadow-sm bg-card rounded-md border border-input focus-within:ring-[3px] focus-within:ring-ring/50 focus-within:border-primary transition-all">
                <Button
                    variant="ghost"
                    size="smallicon"
                    className={`rounded-r-none border-l border-input bg-card hover:bg-accent h-10 w-10 transition-colors shadow-none focus-visible:ring-0 focus-visible:ring-offset-0 ${isEditing ? "text-primary" : "text-muted-foreground"}`}
                    onClick={() => setIsEditing(!isEditing)}
                  >
                    <Pencil size={14} className={isEditing ? "fill-current" : ""} />
                  </Button>
                  <Select
                    value={activeConfigId}
                    onValueChange={requestPresetSwitch}
                  >
                    <SelectTrigger className="w-auto min-w-32 bg-card rounded-none shadow-none border-0 transition-colors hover:bg-accent focus:ring-0 focus-visible:ring-0 focus-visible:ring-offset-0 cursor-pointer">
                      <SelectValue placeholder="Select Config" />
                    </SelectTrigger>
                    <SelectContent>
                      {presets.map((preset) => (
                        <SelectItem key={preset.id} value={preset.id}>
                          <div className="flex items-center justify-between w-full gap-4">
                            <span>{preset.name}</span>
                            <span className="flex items-center gap-1">
                              {usersOf(instances, preset.id).length > 0 && (
                                <span className="text-[10px] text-muted-foreground">
                                  {usersOf(instances, preset.id).join(", ")}
                                </span>
                              )}
                              {preset.id === appliedPresetId && (
                                <span className="text-[10px] bg-primary/10 text-primary border border-primary/20 px-1.5 py-0.5 rounded-full font-bold uppercase tracking-wider">
                                  Active
                                </span>
                              )}
                            </span>
                          </div>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <div className="relative">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="rounded-l-none border-0 border-l border-input px-3 bg-card shadow-none transition-colors hover:bg-accent focus:ring-0 cursor-pointer font-normal"
                      onClick={() => setIsPresetActionsOpen((prev) => !prev)}
                      title="Manage preset files"
                    >
                      <Settings2 size={14} />
                      Manage
                      <ChevronDown size={14} className={isPresetActionsOpen ? "rotate-180 transition-transform" : "transition-transform"} />
                    </Button>
                    {isPresetActionsOpen && (
                      <div className="absolute translate-y-1 w-64 rounded-lg border border-border bg-popover text-foreground shadow-2xl p-2 z-100">
                        <div className="px-2 pt-1 pb-2">
                          <p className="text-sm font-medium">Manage Preset Files</p>
                          <p className="text-xs text-muted-foreground">Create, duplicate, delete, import, or export presets.</p>
                        </div>
                        <Button
                          variant="ghost"
                          className="w-full justify-start h-9 font-normal"
                          onClick={() => {
                            setIsPresetActionsOpen(false);
                            void createPreset();
                          }}
                        >
                          <Plus size={14} />
                          Create Preset
                        </Button>
                        <Button
                          variant="ghost"
                          className="w-full justify-start h-9 font-normal"
                          disabled={!activeConfigId}
                          onClick={() => {
                            setIsPresetActionsOpen(false);
                            void duplicatePreset();
                          }}
                        >
                          <Copy size={14} />
                          Duplicate Preset
                        </Button>
                        <Button
                          variant="ghost"
                          className="w-full justify-start h-9"
                          disabled={presets.length <= 1}
                          onClick={() => {
                            setIsPresetActionsOpen(false);
                            if (presets.length <= 1) return;
                            const users = usersOf(instances, activeConfigId);
                            const ok = window.confirm(
                              users.length > 0
                                ? `Delete this preset? ${users.join(", ")} still ${users.length === 1 ? "runs" : "run"} it -- ` +
                                    "they keep their current settings, but saving here will no longer reach them."
                                : "Delete this preset?",
                            );
                            if (!ok) return;
                            void deletePreset();
                            setIsEditing(false);
                          }}
                        >
                          <Trash2 size={14} />
                          Delete Preset
                        </Button>
                        <div className="my-1 border-t border-border" />
                        <Button
                          variant="ghost"
                          className="w-full justify-start h-9"
                          onClick={() => {
                            setIsPresetActionsOpen(false);
                            openFileDialog();
                          }}
                        >
                          <FolderUp size={14} />
                          Import Preset JSON
                        </Button>
                        <Button
                          variant="ghost"
                          className="w-full justify-start h-9"
                          onClick={() => {
                            setIsPresetActionsOpen(false);
                            exportCurrentConfig();
                          }}
                        >
                          <FolderDown size={14} />
                          Export Preset JSON
                        </Button>
                      </div>
                    )}
                  </div>
                </div>
                <input type="file" ref={fileInputRef} onChange={handleImport} className="hidden" />
              </div>
              )}

              {/* Transitioning Fields */}
              <div className={`flex w-fit gap-4 transition-all duration-300 ease-out overflow-x-hidden pb-2 -mb-2 items-end ${isEditing ? "max-w-200 opacity-100 translate-x-0" : "max-w-0 opacity-0 -translate-x-4 pointer-events-none"
                }`}>
                <div className="h-8 w-px bg-border mb-1" />

                <div className="space-y-1">
                  <label className="text-xs font-thin text-muted-foreground ml-1">Name</label>
                  <Input
                    className="w-42 shadow-sm bg-card"
                    value={config.config_name}
                    onChange={(e) => updateConfig("config_name", e.target.value)}
                  />
                </div>
              </div>

              <div className="space-y-1">
                <label className="text-xs font-thin text-muted-foreground ml-1">Uma <span className="text-[10px] text-muted-foreground">(Theme)</span></label>
                <Select value={effectiveThemeId} onValueChange={(v) => updateConfig("theme", v)}>
                  <SelectTrigger className="min-w-42 shadow-sm bg-card">
                    <SelectValue placeholder="Loading Themes..." />
                  </SelectTrigger>
                  <SelectContent>
                    {themes.filter(t => t && t.id).map((theme) => (
                      <SelectItem key={theme.id} value={theme.id}>
                        <div className="flex items-center gap-2">
                          <div className="w-3 h-3 rounded-full" style={{ backgroundColor: theme.primary }} />
                          {theme.label}
                        </div>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>


            <div className="flex relative gap-3 pl-3">
              <p className="text-sm absolute -top-4 end-px align-right text-muted-foreground -mt-2 w-fit whitespace-nowrap">
                Or press{" "}
                <span className="font-bold text-primary">
                  {(instance?.hotkey ?? "f1").toUpperCase()}
                </span>
                , which works
                without this window focused.
              </p>
              <BotControl />
              <Button
                variant="outline"
                size="icon"
                className="uma-btn h-10 w-10"
                onClick={() => setIsDark(!isDark)}
              >
                {isDark ? <Sun size={18} /> : <Moon size={18} />}
              </Button>
              <Button className="uma-btn font-bold" onClick={() => void handleApplyPreset()}>
                Save &amp; Apply
              </Button>
            </div>
          </div>
        </header>

        {activeDebugFlags.length > 0 && (
          /* Pinned to the header's lower edge and grown upwards, so the border stays an
             unbroken line beneath it however many switches are listed. The 93px is the
             header's own height; a banner positioned from the top instead has to guess
             its own height to keep its underside off the line. */
          <div className="fixed top-0 left-64 right-0 h-[93px] z-200 flex items-end
                          justify-center pointer-events-none">
          <div
            role="status"
            className="mb-1 pointer-events-auto flex items-center gap-3
                       max-w-[min(33rem,calc(100vw-3rem))] px-4 py-2.5 rounded-lg shadow-lg
                       border-1 border-amber-500/50 bg-amber-500/95 text-amber-950
                       dark:bg-amber-500/90 dark:text-amber-950"
          >
            <AlertTriangle className="w-5 h-5 shrink-0" />
            <p className="text-sm leading-snug">
              <span className="font-semibold">Debug mode:</span>{" "}
              {activeDebugFlags.map((flag) => flag.label).join(", ")}.{" "}
              <button
                type="button"
                onClick={() => setActiveTab("debug")}
                className="underline underline-offset-2 font-medium cursor-pointer"
              >
                Turn off
              </button>
            </p>
          </div>
          </div>
        )}
        <Dialog
          open={isDiscardDialogOpen}
          onOpenChange={(open) => {
            setIsDiscardDialogOpen(open);
            if (!open) {
              setPendingConfigSwitchId(null);
            }
          }}
        >
          <DialogContent className="max-w-md">
            <DialogHeader>
              <DialogTitle>Changes could not be saved</DialogTitle>
              <DialogDescription>
                Your edits are normally saved automatically, but that failed &mdash; check
                the bot&apos;s server is running. Switching preset now will discard them.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setIsDiscardDialogOpen(false)}>
                Cancel
              </Button>
              <Button
                variant="destructive"
                onClick={() => {
                  if (pendingConfigSwitchId) {
                    switchToPresetById(pendingConfigSwitchId);
                  }
                  setPendingConfigSwitchId(null);
                  setIsDiscardDialogOpen(false);
                }}
              >
                Discard and Switch
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        <div className="p-6 flex flex-col gap-y-6 w-full min-h-[calc(100vh-6.2rem)] items-center transition-all animate-in fade-in slide-in-from-bottom-2 duration-300">
          {renderContent()}
        </div>
      </div>
    </main>
  );
}

export default App;
