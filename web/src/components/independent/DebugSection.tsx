import { Bug, AlertTriangle } from "lucide-react";
import type { Config, UpdateConfigType } from "@/types";
import { Checkbox } from "../ui/checkbox";
import { Input } from "../ui/input";
import Tooltips from "@/components/_c/Tooltips";

type Props = {
  config: Config;
  updateConfig: UpdateConfigType;
};

export default function DebugSection({ config, updateConfig }: Props) {
  const independent = config.independent_training;

  const update = (patch: Partial<typeof independent>) =>
    updateConfig("independent_training", { ...independent, ...patch });

  const selectOnly = independent.debug_select_skills_only;
  const stopBeforeStart = independent.debug_stop_before_start;
  const forceRefill = independent.debug_force_tp_refill;
  const stopBeforeReroll = independent.debug_stop_before_spark_reroll;
  const pretendShort = independent.debug_pretend_tp_short;
  const waitSeconds = independent.debug_tp_wait_seconds;
  const refillOff = !independent.tp_refill_enabled;
  const neverCarats = independent.tp_refill_strategy === "toughness_only";

  return (
    <div className="section-card">
      <h2 className="text-3xl font-semibold mb-4 flex items-center gap-3">
        <Bug className="text-primary" />
        Debug
      </h2>

      <p className="text-sm text-muted-foreground mb-6">
        Switches for testing the bot's behaviour. These are read each time the bot
        starts, so changing one here and pressing F1 is enough &mdash; nothing needs
        restarting.
      </p>

      <h3 className="text-xl font-semibold mb-2">Career Set-Up</h3>

      <label className={`uma-label col-span-3 ${stopBeforeStart ? "text-amber-600 dark:text-amber-400" : ""}`}>
        <Checkbox
          id="debug-stop-before-start"
          checked={stopBeforeStart}
          onCheckedChange={() =>
            update({ debug_stop_before_start: !stopBeforeStart })
          }
        />
        Stop Before Starting a Career
        <Tooltips>
          Sets a career up completely &mdash; borrow card, training focus, racing style,
          agenda &mdash; then stops on the Final Confirmation screen instead of pressing
          Start. Nothing has been spent at that point, so you can check the whole set-up
          and try again as often as you like. Press Cancel in game to back out.
        </Tooltips>
      </label>

      {stopBeforeStart && (
        <div className="mt-3 flex gap-3 rounded-md border-1 border-amber-500/40 bg-amber-500/10 p-3 text-sm">
          <AlertTriangle className="w-5 h-5 shrink-0 text-amber-600 dark:text-amber-400" />
          <div className="space-y-2">
            <p className="font-medium">No career will actually start while this is on.</p>
            <p className="text-muted-foreground">
              The log reports the training focus, racing style and TP cost it set up, so
              you can check them against the screen. Turn this off to run for real.
            </p>
          </div>
        </div>
      )}

      <h3 className="text-xl font-semibold mt-8 mb-2">Skill Buying</h3>

      <label className={`uma-label col-span-3 ${selectOnly ? "text-amber-600 dark:text-amber-400" : ""}`}>
        <Checkbox
          id="debug-select-skills-only"
          checked={selectOnly}
          onCheckedChange={() => update({ debug_select_skills_only: !selectOnly })}
        />
        Select Skills Without Buying Them
        <Tooltips>
          On the Learn screen, work out what to buy and press + on every chosen skill,
          then stop before Confirm. Selecting costs nothing and is undone by the
          screen's own Reset button, so the same career can be used to test the
          selection as many times as you like.
        </Tooltips>
      </label>

      {selectOnly && (
        <div className="mt-3 flex gap-3 rounded-md border-1 border-amber-500/40 bg-amber-500/10 p-3 text-sm">
          <AlertTriangle className="w-5 h-5 shrink-0 text-amber-600 dark:text-amber-400" />
          <div className="space-y-2">
            <p className="font-medium">
              Careers will not buy skills while this is on.
            </p>
            <p className="text-muted-foreground">
              The bot stops at the Learn screen with everything selected and the points
              still unspent. To run the test again, press <strong>Reset</strong> in the
              game to clear the selection, then F1. Turn this off for a normal career.
            </p>
          </div>
        </div>
      )}

      <p className="mt-4 text-sm text-muted-foreground">
        The command line equivalent is
        <code className="mx-1">--select-skills-only</code>, which does the same thing
        but only for that run.
      </p>

      <h3 className="text-xl font-semibold mt-8 mb-2">Spark Reroll</h3>

      <label className={`uma-label col-span-3 ${stopBeforeReroll ? "text-amber-600 dark:text-amber-400" : ""}`}>
        <Checkbox
          id="debug-stop-before-spark-reroll"
          checked={stopBeforeReroll}
          onCheckedChange={() =>
            update({ debug_stop_before_spark_reroll: !stopBeforeReroll })
          }
        />
        Decide Without Rerolling
        <Tooltips>
          Reads the sparks after a career and decides whether they need a reroll, then
          stops on the Sparks screen instead of pressing Reroll Sparks, so the reading and
          the decision can be checked in the log without spending 30 TP. When it decides
          no reroll is needed, the sparks are confirmed as usual.
        </Tooltips>
      </label>

      <h3 className="text-xl font-semibold mt-8 mb-2">TP Refill</h3>

      <label className={`uma-label col-span-3 ${forceRefill ? "text-amber-600 dark:text-amber-400" : ""}`}>
        <Checkbox
          id="debug-force-tp-refill"
          checked={forceRefill}
          onCheckedChange={() => update({ debug_force_tp_refill: !forceRefill })}
        />
        Open Recover TP Without Buying
        <Tooltips>
          Opens Recover TP on the next home screen even when there is plenty of TP, picks
          what the strategy says, and stops at the dialog with the quantity set instead of
          confirming. Nothing is spent, so the same test can be repeated as often as you
          like &mdash; press Cancel in game and F1 to go again.
        </Tooltips>
      </label>

      {forceRefill && (
        <div className="mt-3 flex gap-3 rounded-md border-1 border-amber-500/40 bg-amber-500/10 p-3 text-sm">
          <AlertTriangle className="w-5 h-5 shrink-0 text-amber-600 dark:text-amber-400" />
          <div className="space-y-2">
            <p className="font-medium">No TP will actually be bought while this is on.</p>
            <p className="text-muted-foreground">
              The bot stops at the dialog with the quantity already set, so the carat
              stepper and the item row can both be checked for free. It also means a
              genuine refill cannot complete while this is on &mdash; turn it off before
              a real run.
            </p>
            {refillOff && (
              <p className="font-medium">
                Refill TP Automatically is off, so nothing will happen. Turn it on for
                the test.
              </p>
            )}
            {!refillOff && neverCarats && (
              <p className="font-medium">
                The strategy is Toughness 30 only, so this will open the item dialog and
                never the carats one. Choose a carats strategy to see that dialog.
              </p>
            )}
          </div>
        </div>
      )}

      <h3 className="text-xl font-semibold mt-8 mb-2">Waiting for TP</h3>

      <label className={`uma-label col-span-3 ${pretendShort ? "text-amber-600 dark:text-amber-400" : ""}`}>
        <Checkbox
          id="debug-pretend-tp-short"
          checked={pretendShort}
          onCheckedChange={() => update({ debug_pretend_tp_short: !pretendShort })}
        />
        Pretend There Is Never Enough TP
        <Tooltips>
          Makes the next career look unaffordable, so the TP wait can be watched without
          an account that is really short and four hours to spare. It goes straight to the
          wait and never opens Recover TP, so it cannot spend carats. It fires
          &lsquo;once per session &mdash; otherwise the career stays unaffordable forever
          and you never see the wait end.
        </Tooltips>
      </label>

      {pretendShort && (
        <div className="mt-3 flex gap-3 rounded-md border-1 border-amber-500/40 bg-amber-500/10 p-3 text-sm">
          <AlertTriangle className="w-5 h-5 shrink-0 text-amber-600 dark:text-amber-400" />
          <div className="space-y-2">
            <p className="font-medium">The next career will be held back once.</p>
            <p className="text-muted-foreground">
              The bot defers it, runs Team Trials if it is due, idles until the wait ends,
              and then starts the career normally. Set a ceiling below so you are not
              watching a four-hour hold. Press F1 again for another go.
            </p>
          </div>
        </div>
      )}

      <label className="uma-label mt-4">
        <span>Shorten The Wait To (seconds)</span>
        <Input
          className="w-24"
          type="number"
          min={0}
          value={waitSeconds}
          onChange={(e) =>
            update({ debug_tp_wait_seconds: Math.max(0, e.target.valueAsNumber || 0) })
          }
        />
        <Tooltips>
          A ceiling on the computed TP wait, so a four-hour hold can be watched in a
          minute. Zero is off. It only ever shortens the wait &mdash; it cannot make the
          bot wait longer than the real arithmetic says.
        </Tooltips>
      </label>

      <p className="mt-4 text-sm text-muted-foreground">
        The command line equivalents are
        <code className="mx-1">--pretend-tp-short</code> and
        <code className="mx-1">--tp-wait-seconds 90</code>, which apply to that run only.
      </p>
    </div>
  );
}
