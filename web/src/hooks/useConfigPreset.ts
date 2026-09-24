import { useState, useEffect, useCallback, useRef } from "react";
import type { Config } from "../types";

export type ConfigEntry = {
  id: string;
  name: string;
  config: Config;
};

async function getConfigFromServer(configId: string): Promise<ConfigEntry | null> {
  // Was a synchronous XMLHttpRequest, called during render -- so every keystroke froze
  // the page until the server replied. The server shares a process with the bot, whose
  // OCR and screenshots saturate it, so on a machine actually running emulators the
  // config page locked up for as long as a capture took.
  try {
    const res = await fetch(`/configs/${configId}`);
    if (!res.ok) return null;
    const data = await res.json();
    return data.config ?? null;
  } catch (e) {
    console.error(e);
    return null;
  }
}

export function useConfigPreset() {
  const [configs, setConfigs] = useState<ConfigEntry[]>([]);
  const [activeConfigId, setActiveConfigId] = useState<string>("");
  const [appliedPresetId, setAppliedPresetIdState] = useState<string>("");
  // The preset list has been asked for and answered (or failed to be). Until then the
  // page's preset is the template, and App will not save it over anything.
  const [presetsReady, setPresetsReady] = useState(false);

  useEffect(() => {
    let isMounted = true;

    const initialize = async () => {
      try {
        const [configsRes] = await Promise.all([
          fetch("/configs")
        ]);
        const [presetIdRes] = await Promise.all([
          fetch("/config/applied-preset")
        ]);
        if (!configsRes.ok) {
          throw new Error("Failed to fetch initial configuration data");
        }

        const [configsData] = await Promise.all([
          configsRes.json(),
        ]);
        const [appliedIdData] = await Promise.all([
          presetIdRes.json()
        ]);
        const appliedId = appliedIdData.preset_id

        if (!isMounted) return;

        const normalized = Array.isArray(configsData?.configs)
          ? configsData.configs
          : [];

        setConfigs(normalized);
        setAppliedPresetIdState(appliedId);

        if (normalized.length > 0) {
          const initialId = (appliedId && normalized.some((c: ConfigEntry) => c.id === appliedId))
            ? appliedId
            : normalized[0].id;

          setActiveConfigId((prev) => prev || initialId);
        } else {
          setActiveConfigId("");
        }
      } catch (error) {
        console.error("Failed to initialize configuration presets:", error);
      } finally {
        if (isMounted) setPresetsReady(true);
      }
    };

    void initialize();
    return () => {
      isMounted = false;
    };
  }, []);

  const savePresetById = useCallback(async (presetId: string, config: Config) => {
    const res = await fetch(`/configs/${presetId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    if (!res.ok) {
      throw new Error(`Failed to save config. HTTP status: ${res.status}`);
    }
    setConfigs((prev) => prev.map((entry) => (
      entry.id === presetId
        ? { ...entry, name: config.config_name || entry.name, config }
        : entry
    )));
  }, []);

  const savePreset = useCallback(async (config: Config) => {
    if (!activeConfigId) return;
    await savePresetById(activeConfigId, config);
  }, [activeConfigId, savePresetById]);

  const createPreset = useCallback(async (): Promise<ConfigEntry | null> => {
    try {
      const res = await fetch("/configs", {
        method: "POST",
    });
      if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
      const data = await res.json();
      const created = data?.config;
      if (!created) return null;
      setConfigs((prev) => [...prev, created]);
      setActiveConfigId(created.id);
      return created;
    } catch (error) {
      console.error("Failed to create config:", error);
      return null;
    }
  }, []);

  const duplicatePreset = useCallback(async () => {
    if (!activeConfigId) return;
    try {
      const res = await fetch(`/configs/${activeConfigId}/duplicate`, {
        method: "POST",
      });
      if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
      const data = await res.json();
      const duplicated = data?.config;
      if (!duplicated) return;
      setConfigs((prev) => [...prev, duplicated]);
      setActiveConfigId(duplicated.id);
    } catch (error) {
      console.error("Failed to duplicate config:", error);
    }
  }, [activeConfigId]);

  const deletePreset = useCallback(async () => {
    if (!activeConfigId) return;
    try {
      const res = await fetch(`/configs/${activeConfigId}`, {
        method: "DELETE",
      });
      if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
      setConfigs((prev) => {
        const next = prev.filter((entry) => entry.id !== activeConfigId);
        const stillActive = next.some((entry) => entry.id === activeConfigId);
        if (!stillActive) {
          setActiveConfigId(next[0]?.id ?? "");
        }
        return next;
      });
    } catch (error) {
      console.error("Failed to delete config:", error);
      alert("Could not delete config. At least one config file must remain.");
    }
  }, [activeConfigId]);

  // A POST, not a GET. This used to read the endpoint and then set local state, so the
  // UI called the new preset applied while the disk kept pointing at the old one, and a
  // refresh put it back. Nothing else writes this pointer.
  const setAppliedPresetId = useCallback(async (presetId: string) => {
    const res = await fetch("/config/applied-preset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preset_id: presetId }),
    });
    if (!res.ok) {
      throw new Error(`Failed to save applied preset id. HTTP status: ${res.status}`);
    }
    setAppliedPresetIdState(presetId);
  }, []);

  const activeIndex = configs.findIndex((entry) => entry.id === activeConfigId);
  const resolvedIndex = activeIndex === -1 ? 0 : activeIndex;

  // The chosen preset as the server last gave it, or as the page last saved it -- what the
  // workspace is compared against to know whether there is anything unsaved.
  //
  // It used to be refreshed only by fetching, and nothing fetched after a save: the
  // comment here said reloadActiveConfig was "for the callers that have just written it",
  // and no caller did. So after an auto-save this still held the preset from before the
  // edit, and App rebuilt the workspace from it -- a skill removed on the Skills tab came
  // back on the page a second later while the server no longer had it, and the next edit
  // posted the page's copy and put it back there too. Hence "it takes several tries".
  const [activeConfig, setActiveConfig] = useState<ConfigEntry | null>(null);
  const [activeConfigEpoch, setActiveConfigEpoch] = useState(0);
  const reloadActiveConfig = useCallback(() => setActiveConfigEpoch((n) => n + 1), []);
  // Counts completed loads -- a fetch landing, or the preset being cleared. App rebuilds
  // the workspace on this and nothing else, so a save, which changes activeConfig through
  // acceptSaved below, never does: an edit made while that save was in flight survives.
  const [activeConfigLoads, setActiveConfigLoads] = useState(0);

  useEffect(() => {
    if (!activeConfigId) {
      setActiveConfig(null);
      setActiveConfigLoads((n) => n + 1);
      return;
    }
    let alive = true;
    void getConfigFromServer(activeConfigId).then((found) => {
      if (!alive) return;
      setActiveConfig(found);
      setActiveConfigLoads((n) => n + 1);
    });
    return () => {
      alive = false;
    };
  }, [activeConfigId, activeConfigEpoch]);

  // The page's own edits, just written: they are what the server holds now, so they are
  // the new baseline. Only for saves of what the page shows -- an import saves content the
  // page has never shown, and reloads instead.
  const acceptSaved = useCallback((presetId: string, config: Config) => {
    setActiveConfig((prev) =>
      prev && prev.id === presetId ? { ...prev, name: config.config_name || prev.name, config } : prev
    );
  }, []);

  // A preset is shared: saving it on one instance's page writes it through to every
  // instance that applies it. A second page open on another instance kept showing the
  // old values, and its next edit auto-saved that stale state back over the change --
  // both pages saying "Saved". Re-read when this page is looked at again, which is when
  // it is about to be edited.
  // Set by App to "there are edits on this page that the disk does not have yet".
  // Declared as a ref because the dirty flag is derived from the config this hook
  // returns, so it does not exist yet at the point this hook is called.
  const holdReloadRef = useRef<() => boolean>(() => false);

  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState !== "visible") return;
      // Re-reading here would replace the workspace with the file, so an edit made
      // inside the auto-save debounce -- or one whose save failed -- would be dropped by
      // the act of switching to another window and back. Whoever has unsaved work wins;
      // the auto-save that follows writes it through, and the next focus re-reads.
      if (holdReloadRef.current()) return;
      reloadActiveConfig();
    };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [reloadActiveConfig]);

  return {
    activeIndex: resolvedIndex,
    activeConfig,
    activeConfigLoads,
    acceptSaved,
    reloadActiveConfig,
    activeConfigId,
    appliedPresetId,
    presets: configs,
    setActiveIndex: (index: number) => {
      if (index < 0 || index >= configs.length) return;
      setActiveConfigId(configs[index].id);
    },
    savePresetById,
    savePreset,
    createPreset,
    duplicatePreset,
    deletePreset,
    setAppliedPresetId,
    holdReloadRef,
    presetsReady,
  };
}
