import { z } from "zod";

export type SkillData = {
  name: string;
  description: string;
  iconid: string;
  id: string;
};

export const SkillSchema = z.looseObject({
  is_auto_buy_skill: z.boolean(),
  skill_list: z.array(z.string()),
  // Skills never to be bought. Family-level at runtime: one entry blocks every tier of
  // that skill, so it is not an ordered list and nothing here needs a rank. Mutually
  // exclusive with skill_list -- the picker moves a skill between them.
  skill_blacklist: z.array(z.string()).default([]),
});

export type Skill = z.infer<typeof SkillSchema>;
