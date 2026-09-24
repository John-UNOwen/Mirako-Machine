import { z } from "zod";
import { SkillSchema } from "./skill.type";
import { IndependentTrainingSchema } from "./independent-training.type";
import { TeamTrialsSchema } from "./team-trials.type";
import { WebhookSchema } from "./webhook.type";

// Loose on purpose, here and in every nested block -- see the other files in this
// directory. A strict z.object drops keys it does not declare, and the UI posts whatever
// survives parsing, so saving any page used to delete config blocks the UI knows nothing
// about. The top level was fixed first and the nested blocks were left strict, which
// still lost keys on the one path that runs the schema: importing a config. A file from
// a newer build came in, its unknown sub-keys were dropped on parse, the new preset was
// built from default.json which never had them, and the dialog said "imported".
// The server merges defensively too; this is the near half of that.
export const ConfigSchema = z.looseObject({
  config_name: z.string(),
  theme: z.string().default("hishi-miracle"),
  sleep_time_multiplier: z.number(),
  use_adb: z.boolean(),
  device_id: z.string(),
  ocr_use_gpu: z.boolean(),
  auto_check_updates: z.boolean(),
  notifications_enabled: z.boolean(),
  error_notification: z.string(),
  success_notification: z.string(),
  notification_volume: z.number(),
  skill: SkillSchema,
  window_name: z.string(),
  preset_id: z.string(),
  independent_training: IndependentTrainingSchema.default(
    IndependentTrainingSchema.parse({})),
  team_trials: TeamTrialsSchema.default(TeamTrialsSchema.parse({})),
  webhook: WebhookSchema.default(WebhookSchema.parse({})),
});

export type Config = z.infer<typeof ConfigSchema>;

export type UpdateConfigType = <K extends keyof Config>(
  key: K,
  value: Config[K]
) => void;
