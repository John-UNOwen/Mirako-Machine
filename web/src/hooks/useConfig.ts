import { useCallback, useState } from "react";
import type { Config } from "../types";

export function useConfig(defaultConfig: Config) {
  const [config, setConfig] = useState<Config>(defaultConfig);
  const [toast, setToast] = useState<{ show: boolean; message: string; isError?: boolean }>({
    show: false,
    message: "",
  });

  const triggerToast = useCallback((message: string, isError = false) => {
    setToast({ show: true, message, isError });
    setTimeout(() => setToast({ show: false, message: "", isError: false }), 3000);
  }, []);

  // `silent` suppresses only the success toast. A failed save is always announced --
  // with auto-saving there is no button left whose absence would tell you it did not work.
  //
  // Stable across renders. It was a fresh function every render, which made App's
  // auto-save callback fresh every render too, and the effect keyed on that callback ran
  // after every render: it flipped "Saved" back to idle in the frame it appeared, and
  // restarted the save's wait whenever anything else on the page re-rendered.
  const saveConfig = useCallback(async (nextConfig: Config, silent = false) => {
    try {
      const res = await fetch("config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(nextConfig),
      });
      if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);

      await res.json();
      if (!silent) triggerToast("Configuration changed successfully!");
    } catch (error) {
      console.error(error);
      triggerToast("Failed to save configuration.", true);
      // Re-raised as well as announced. Auto-save has no caller that cares, but the
      // apply flow does: it used to carry on and mark the preset applied although the
      // write that makes it applied had failed, leaving the page and the disk disagreeing
      // about which preset the bot runs until the next reload.
      throw error;
    }
  }, [triggerToast]);

  return { config, setConfig, saveConfig, toast, notify: triggerToast };
}
