import { z } from "zod";

// Discord notifications. The URL doubles as the on/off switch: with none set nothing is
// sent, which is why there is no separate "enabled" flag beside it.
export const WebhookSchema = z.looseObject({
  url: z.string().default(""),
  skills_enabled: z.boolean().default(true),
  career_summary_enabled: z.boolean().default(true),
  recovery_enabled: z.boolean().default(true),
  // The spark choice after a reroll is asked through a Discord bot, not the webhook: a
  // webhook can only post, and the answer is a reaction the bot reads back. Both empty
  // means no bot, and the reroll is not attempted.
  bot_token: z.string().default(""),
  // Where the question goes: a server channel, or a direct message to one person. For a DM
  // the person adds the app to their own account (a user install), or shares a server
  // with it: Discord refuses a bot's DM to anyone else.
  choice_target: z.enum(["channel", "dm"]).default("channel"),
  choice_channel_id: z.string().default(""),
  choice_user_id: z.string().default(""),
});

export type Webhook = z.infer<typeof WebhookSchema>;
