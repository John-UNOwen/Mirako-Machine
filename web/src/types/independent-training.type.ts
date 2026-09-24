import { z } from "zod";

// Independent Training is opt-in and self-contained, so every field carries a default.
// A preset saved before the mode existed then parses cleanly with the mode simply off,
// rather than failing validation over a block the user may never touch.
export const IndependentTrainingSchema = z.looseObject({
  max_runs: z.number().default(0),
  after_max_runs: z.enum(["stop", "dailies"]).default("stop"),
  wait_poll_seconds: z.number().default(60),
  training_minutes: z.number().default(50),
  // Paths to the card artwork templates, in priority order.
  borrow_cards: z.array(z.string()).default([]),
  borrow_warn_every_refreshes: z.number().default(10),
  spend_leftover_points: z.boolean().default(false),
  // Which extras the leftover walk reaches first. "bottom_up" takes the end of the
  // game's list; "best_value" takes the most heavily discounted. Ignored entirely when
  // maximize_rating is on, which replaces the walk rather than reordering it.
  leftover_strategy: z.enum(["bottom_up", "best_value"]).default("bottom_up"),
  // Choose the leftover skills worth the most rating rather than walking the list in
  // some order. Solved as a knapsack against the trainee's aptitudes, read off the
  // Complete Career screen each career. A different question from leftover_strategy:
  // that one orders a walk, this one decides what the combination should be.
  maximize_rating: z.boolean().default(false),
  // Training Focus to set before a career starts. "default" leaves the game's own
  // setting alone; anything else is clicked only when it is not already selected.
  training_focus: z
    .enum(["default", "balanced", "stamina", "sprint"])
    .default("default"),
  // Scenario to pick on Scenario Select. "default" takes whatever the game has
  // selected; anything else turns the carousel until that one is showing, and stops the
  // bot rather than starting a career in a different scenario if it cannot be found.
  scenario: z
    .enum(["default", "ura_finale", "unity_cup", "trackblazer", "grand_concert"])
    .default("default"),
  // Support deck to select on Support Formation: 1-10 by position, 0 to leave whatever
  // is showing. deck_name, when not empty, overrides it and is matched against the name
  // on the deck -- the game allows 1-10 characters.
  deck: z.number().int().min(0).max(10).default(0),
  deck_name: z.string().max(10).default(""),
  // Saved race agenda to load on My Agendas, by its position: 1 is the top of the list and
  // what the bot loaded before this existed. agenda_name, when not empty, overrides it and
  // takes the first agenda with that name -- the game lets two agendas share one. Not
  // length-capped here: the game's own limit is not known, and a cap below it would
  // quietly cut a real name down on the next save.
  agenda_slot: z.number().int().min(1).max(8).default(1),
  agenda_name: z.string().default(""),
  // Racing style to set before a career starts. "default" leaves the trainee's own
  // style alone and skips the dialog entirely.
  racing_style: z
    .enum(["default", "front", "pace", "late", "end"])
    .default("default"),
  tp_refill_enabled: z.boolean().default(false),
  // Which item pays for TP. "toughness_only" never spends carats; "toughness_first"
  // falls back to them once the free items run out; "carats_first" saves the free
  // items for later. tp_refill_min_carats_remaining is honoured by both carat modes.
  tp_refill_strategy: z
    .enum(["toughness_only", "toughness_first", "carats_first"])
    .default("toughness_first"),
  tp_refill_max_per_session: z.number().default(0),
  tp_refill_min_carats_remaining: z.number().default(0),
  // Debug: select skills on the Learn screen but stop before committing them, so the
  // selection can be checked and re-run without burning a career. Surfaced in the
  // Debug tab; --select-skills-only does the same thing from the command line.
  debug_select_skills_only: z.boolean().default(false),
  // Debug: set a career up completely -- borrow card, training focus, racing style,
  // agenda -- then stop instead of pressing Start. Nothing has been spent at that
  // point, so the whole setup can be checked and retried for free.
  debug_stop_before_start: z.boolean().default(false),
  // Debug: open Recover TP once on the next home screen whatever the TP balance, then
  // stop at the dialog with the quantity set rather than confirming. Exercises the
  // refill screens without waiting to run out and without spending anything, so the
  // test repeats for free. While it is on, no refill can complete -- including a real
  // one.
  debug_force_tp_refill: z.boolean().default(false),
  // Running out of TP: a wait, or the end of the session. The task queue defers the
  // career by the shortfall and runs something else -- usually Team Trials -- rather
  // than stopping. Off restores the old behaviour exactly.
  // The two daily chores on the home screen. Free collection, idempotent, so both
  // default on; either can be turned off without touching the other.
  // How long to hold the whole queue when the account turns up signed in on another
  // device. 0 stops instead, which is what it did before the queue existed.
  session_conflict_wait_minutes: z.number().int().min(0).default(60),
  collect_missions: z.boolean().default(true),
  collect_presents: z.boolean().default(true),
  daily_races_enabled: z.boolean().default(false),
  daily_race_program: z.enum(["moonlight_sho", "jupiter_cup"]).default("moonlight_sho"),
  daily_race_difficulty: z.enum(["very_hard", "hard", "normal", "easy"]).default("very_hard"),
  daily_race_tickets_per_day: z.number().int().min(1).max(6).default(6),
  wait_for_tp: z.boolean().default(true),
  // Debug: treat every career as unaffordable, so the wait above can be exercised
  // without an account that is really out of TP. Reaches the wait past the refill
  // guards rather than through them, so it can never spend carats.
  debug_pretend_tp_short: z.boolean().default(false),
  // Debug: cap the computed TP wait, so a four-hour hold can be watched in a minute.
  // Zero is off, and it only ever shortens.
  debug_tp_wait_seconds: z.number().int().min(0).default(0),
  // Restart the game after the kinds of stuck run a restart can clear, rather than
  // stopping. Budgeted, and it stops for real once one problem has used up its restarts.
  restart_on_stuck: z.boolean().default(true),
  // Ten, so the five-in-a-row default for one problem below is reachable, with room
  // left for others. At three the budget ran out before one problem used its own.
  restart_max_per_session: z.number().int().min(0).default(10),
  // Restarts in a row for the same problem before it stops for real. The emulator
  // sometimes needs several to come back from a freeze; a problem no restart can
  // fix never comes back, and this is what bounds how long it is tried.
  restart_max_same_kind: z.number().int().min(1).default(5),
  // Empty means read the package off the device, which is what it should normally be:
  // the JP and global builds differ, so a hardcoded default is wrong for somebody.
  game_package: z.string().default(""),
});

export type IndependentTraining = z.infer<typeof IndependentTrainingSchema>;
