// Pure helpers for the instance tabs and the preset bar. Kept free of imports so
// devtools/check_instance_presets.py can run them under plain node.

export type InstanceLike = {
  declared: boolean;
  name: string;
  current?: boolean;
  preset_id?: string;
  preset_name?: string | null;
};

// The default instance is config.json with no name; its label is its hotkey, which
// means nothing on a tab.
export const tabLabel = (row: InstanceLike): string => (row.declared ? row.name : "Default");

// The preset a tab runs, by the name the preset list shows. Falls back to the id for an
// instance whose preset is not in this page's list -- deleted, or not loaded yet.
export const presetLabel = (row: InstanceLike): string =>
  row.preset_name || row.preset_id || "";

// Which *other* instances run a preset -- what saving it will reach besides this one.
// This page's own instance is left out: the preset bar already marks it Active.
export const usersOf = (rows: InstanceLike[], presetId: string): string[] =>
  presetId ? rows.filter((row) => !row.current && row.preset_id === presetId).map(tabLabel) : [];
