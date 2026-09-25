import { z } from "zod";

// Discord notifications. The URL doubles as the on/off switch: with none set nothing is
// sent, which is why there is no separate "enabled" flag beside it.
export const WebhookSchema = z.looseObject({
  url: z.string().default(""),
  skills_enabled: z.boolean().default(true),
  career_summary_enabled: z.boolean().default(true),
  recovery_enabled: z.boolean().default(true),
  // The link to the Mirako bot (core/relay_client.py), swapped for the code /link gives
  // in its DMs. The spark choice is asked through it, not the webhook: a webhook can only
  // post. Empty means not linked, and the reroll is not attempted.
  relay_token: z.string().default(""),
});

export type Webhook = z.infer<typeof WebhookSchema>;
