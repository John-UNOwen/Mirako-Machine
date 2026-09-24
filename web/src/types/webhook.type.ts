import { z } from "zod";

// Discord notifications. The URL doubles as the on/off switch: with none set nothing is
// sent, which is why there is no separate "enabled" flag beside it.
export const WebhookSchema = z.looseObject({
  url: z.string().default(""),
  skills_enabled: z.boolean().default(true),
  career_summary_enabled: z.boolean().default(true),
  recovery_enabled: z.boolean().default(true),
});

export type Webhook = z.infer<typeof WebhookSchema>;
