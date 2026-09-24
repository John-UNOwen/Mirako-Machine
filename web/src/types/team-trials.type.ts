import { z } from "zod";

export const TeamTrialsSchema = z.looseObject({
  // Run Team Trials between careers, spending the RP that accrues while a career runs.
  // Off by default: it spends a resource, and a bot that quietly starts doing so because
  // it was updated is a bot nobody asked.
  enabled: z.boolean().default(false),
  // Stop once fewer than this many charges remain, rather than draining the bar. RP
  // refills one charge every two hours to a maximum of five, so a floor above zero keeps
  // some in hand for playing by hand.
  keep_charges: z.number().int().min(0).max(5).default(0),
  // Rarely, one of the three opponents carries a "With Every Win!" badge, which pays a
  // reward per win on top of the usual standings movement. On by default: taking it only
  // ever adds a reward, and a miss falls back to the top opponent, which is what the bot
  // did before this existed.
  prioritise_reward: z.boolean().default(true),
});

export type TeamTrials = z.infer<typeof TeamTrialsSchema>;
