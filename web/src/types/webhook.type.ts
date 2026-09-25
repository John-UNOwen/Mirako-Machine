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
  // Who the question goes to, as a direct message. The person adds the app to their own
  // account (a user install): Discord refuses a bot's DM to anyone who has not, unless
  // they share a server.
  choice_user_id: z.string().default(""),
  // The link to the shared Mirako bot (core/relay_client.py), swapped for the code /link
  // gives in its DMs. Set, it is used in place of the bot above.
  relay_token: z.string().default(""),
});

export type Webhook = z.infer<typeof WebhookSchema>;
