import { useEffect, useMemo, useState } from "react";
import { BarChart3, RotateCcw, X } from "lucide-react";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import Tooltips from "@/components/_c/Tooltips";

type Run = {
  finished_at: string | null;
  duration_seconds: number | null;
  duration_estimated?: boolean;
  fans: number | null;
  races: number | null;
  wins: number | null;
  speed: number | null;
  stamina: number | null;
  power: number | null;
  guts: number | null;
  wit: number | null;
  skill_points: number | null;
  carats_earned: number | null;
  tp_refills: number | null;
};

const RANGES = [
  { id: "today", label: "Today", days: 0 },
  { id: "7", label: "7 days", days: 7 },
  { id: "30", label: "30 days", days: 30 },
  { id: "all", label: "All time", days: Infinity },
] as const;

type RangeId = (typeof RANGES)[number]["id"];

/** Start of the window a range covers. "Today" means since local midnight, not 24h ago. */
function cutoffFor(days: number): number {
  const now = new Date();
  if (days === 0) {
    return new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  }
  if (!Number.isFinite(days)) return 0;
  return now.getTime() - days * 86_400_000;
}

/**
 * Local midnight on a YYYY-MM-DD date, `offsetDays` later. Null for the empty string an
 * unfilled date input gives, and for anything else that is not a plain date.
 *
 * Picked apart by hand rather than handed to `new Date(iso)`, which reads the bare
 * "2026-08-31" form as UTC: anywhere west of Greenwich that lands on the evening of the
 * 30th and the whole window slips a day. The day arithmetic goes through the constructor
 * for a related reason -- it normalises the overflow, where adding 86_400_000ms comes out
 * an hour off either side of a daylight-saving change.
 */
function dayStart(iso: string, offsetDays = 0): number | null {
  const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!parts) return null;
  return new Date(
    Number(parts[1]),
    Number(parts[2]) - 1,
    Number(parts[3]) + offsetDays
  ).getTime();
}

/** A picked date as "31 Aug", for the caption over the summary. */
function formatDay(iso: string): string {
  const time = dayStart(iso);
  if (time === null) return "";
  return new Date(time).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

/** What the caption over the summary reads. Custom dates win; with neither set, both
 *  arguments are empty and the preset's own label falls through. */
function rangeCaption(presetLabel: string, fromIso: string, toIso: string): string {
  if (fromIso && toIso) return `${formatDay(fromIso)} – ${formatDay(toIso)}`;
  if (fromIso) return `Since ${formatDay(fromIso)}`;
  if (toIso) return `Until ${formatDay(toIso)}`;
  return presetLabel;
}

/** Sum, skipping unread values. Returns null when nothing was readable at all. */
function sum(runs: Run[], key: keyof Run): number | null {
  const values = runs
    .map((r) => r[key])
    .filter((v): v is number => typeof v === "number");
  return values.length ? values.reduce((a, b) => a + b, 0) : null;
}

function formatNumber(value: number | null | undefined, digits = 0): string {
  // Undefined as well as null: a row written before a field existed has no key for it at
  // all, so every column added from here on arrives undefined on the old rows. Guarding
  // only null meant adding one field blanked the whole tab for anyone with history.
  if (value === null || value === undefined) return "—";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
}

function formatWhen(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function StatisticsSection() {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [range, setRange] = useState<RangeId>("7");
  // A window of the user's own, which takes over from the presets as soon as either end
  // is filled in. Held as the empty string rather than null because that is what an
  // unfilled date input reads as, and what it has to be set back to in order to clear it.
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      const res = await fetch("/stats/runs");
      if (!res.ok) throw new Error(String(res.status));
      const data = await res.json();
      setRuns(data.runs ?? []);
      setError(null);
    } catch {
      setError("Could not load the run history. Is the bot's server running?");
    }
  };

  useEffect(() => {
    load();
  }, []);

  const active = RANGES.find((r) => r.id === range) ?? RANGES[1];
  const usingCustom = customFrom !== "" || customTo !== "";

  // Half-open [from, to). The end date becomes midnight on the day *after* the one
  // picked, so the day chosen counts in full -- otherwise the same date at both ends
  // would select the single instant of its midnight and come back empty. An end left
  // blank is simply open: 0 and Infinity are outside any real timestamp.
  //
  // Computed in here rather than inline so that "7 days" is not re-derived from a fresh
  // Date on every render, which would leave the filter below memoised on a number that
  // changes every millisecond.
  const { from, to } = useMemo(() => {
    if (usingCustom) {
      return { from: dayStart(customFrom) ?? 0, to: dayStart(customTo, 1) ?? Infinity };
    }
    return { from: cutoffFor(active.days), to: Infinity };
  }, [usingCustom, customFrom, customTo, active]);

  // Reachable by typing into the field, which browsers allow whatever min/max say.
  const invertedRange = usingCustom && to <= from;

  const inRange = useMemo(() => {
    if (!runs) return [];
    return runs.filter((r) => {
      if (!r.finished_at) return false;
      const t = new Date(r.finished_at).getTime();
      return !Number.isNaN(t) && t >= from && t < to;
    });
  }, [runs, from, to]);

  const summary = useMemo(() => {
    const careers = inRange.length;
    const fans = sum(inRange, "fans");
    const withFans = inRange.filter((r) => typeof r.fans === "number").length;
    const seconds = sum(inRange, "duration_seconds");
    const estimated = inRange.filter((r) => r.duration_estimated).length;

    // Careers per day is measured over the span actually covered by the records, not the
    // nominal range: two careers in a fresh install is 2/day, not 0.3/day because the
    // tab happens to say 7 days.
    const times = inRange
      .map((r) => new Date(r.finished_at ?? "").getTime())
      .filter((t) => !Number.isNaN(t));
    const spanDays =
      times.length > 1
        ? Math.max(1, (Math.max(...times) - Math.min(...times)) / 86_400_000)
        : 1;

    return {
      careers,
      fans,
      fansPerCareer: fans !== null && withFans ? fans / withFans : null,
      careersPerDay: careers ? careers / spanDays : null,
      carats: sum(inRange, "carats_earned"),
      refills: sum(inRange, "tp_refills"),
      seconds,
      estimated,
    };
  }, [inRange]);

  const reset = async () => {
    if (!window.confirm("Delete the recorded run history? The file is moved aside, not erased.")) {
      return;
    }
    await fetch("/stats/reset", { method: "POST" });
    load();
  };

  const cards: { label: string; value: string; hint?: string }[] = [
    { label: "IT Careers", value: formatNumber(summary.careers) },
    { label: "Fans", value: formatNumber(summary.fans) },
    { label: "Fans / Career", value: formatNumber(summary.fansPerCareer) },
    { label: "Careers / Day", value: formatNumber(summary.careersPerDay, 1) },
    {
      label: "Carats",
      value: formatNumber(summary.carats),
      hint: "Awarded by the career, read off the Items Obtained grid on the Training Log's Career page.",
    },
    {
      label: "Refills",
      value: formatNumber(summary.refills),
      hint: "TP refills bought to afford these careers, items and carats alike. Counted rather than read, and charged to the career the refill paid for.",
    },
    {
      label: "Run Time",
      value: formatDuration(summary.seconds),
      hint: summary.estimated
        ? `Timed from the moment the bot pressed Start. ${summary.estimated} of these ` +
          `${summary.careers} careers were already running when the bot joined them, so ` +
          `they count as a typical 50m rather than a measured time.`
        : "Timed from the moment the bot pressed Start until the Training Log was read.",
    },
  ];

  return (
    <div className="section-card">
      <div className="flex items-center justify-between gap-4 mb-5 flex-wrap">
        <h2 className="text-3xl font-semibold flex items-center gap-3">
          <BarChart3 className="text-primary" />
          Statistics
        </h2>
        <Button variant="destructive" onClick={reset} className="gap-2">
          <RotateCcw className="w-4 h-4" />
          Reset
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-3 mb-7">
        <div className="inline-flex rounded-md border-1 border-border overflow-hidden">
          {RANGES.map((r) => (
            <button
              key={r.id}
              type="button"
              onClick={() => {
                setRange(r.id);
                // A preset and a hand-picked window are alternatives rather than filters
                // that stack, so choosing one drops the other.
                setCustomFrom("");
                setCustomTo("");
              }}
              className={`px-4 py-1.5 text-sm transition-colors cursor-pointer ${
                range === r.id && !usingCustom
                  ? "bg-primary text-primary-foreground font-medium"
                  : "text-muted-foreground hover:bg-accent"
              }`}
            >
              {r.label}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <label className="uma-label">
            <span className="text-sm text-muted-foreground">From</span>
            <Input
              type="date"
              aria-label="Show careers finished on or after this date"
              value={customFrom}
              max={customTo || undefined}
              onChange={(e) => setCustomFrom(e.target.value)}
              className="dark:[&::-webkit-calendar-picker-indicator]:invert"
            />
          </label>
          <label className="uma-label">
            <span className="text-sm text-muted-foreground">To</span>
            <Input
              type="date"
              aria-label="Show careers finished on or before this date"
              value={customTo}
              min={customFrom || undefined}
              onChange={(e) => setCustomTo(e.target.value)}
              className="dark:[&::-webkit-calendar-picker-indicator]:invert"
            />
          </label>
          <Tooltips>
            A window of your own, which takes over from the buttons on the left the
            moment either date is set. Both ends count in full, so the same date twice
            covers that whole day. Leave one empty to leave that end open.
          </Tooltips>
          {usingCustom && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setCustomFrom("");
                setCustomTo("");
              }}
              className="text-muted-foreground"
            >
              <X className="w-4 h-4" />
              Clear
            </Button>
          )}
        </div>
      </div>

      {error && <p className="text-sm text-destructive mb-4">{error}</p>}
      {invertedRange && (
        <p className="text-sm text-destructive mb-4">
          From is after To, so no career can fall between them.
        </p>
      )}

      <div className="flex items-baseline justify-between mb-2">
        <span className="text-xs uppercase tracking-widest text-muted-foreground">
          Summary
        </span>
        <span className="text-xs uppercase tracking-widest text-muted-foreground">
          {rangeCaption(active.label, customFrom, customTo)}
        </span>
      </div>

      <div className="grid gap-3 mb-8 grid-cols-2 md:grid-cols-4 lg:grid-cols-7">
        {cards.map((c) => (
          <div key={c.label} className="rounded-lg border-1 border-border p-4">
            <div className="text-xs uppercase tracking-wider text-muted-foreground flex items-center gap-1">
              {c.label}
              {c.hint && <Tooltips>{c.hint}</Tooltips>}
            </div>
            <div className="text-3xl font-semibold mt-1.5 tabular-nums">{c.value}</div>
          </div>
        ))}
      </div>

      <h3 className="text-xl font-semibold mb-1 flex items-center gap-2">
        Run History
        <Tooltips>
          One row per completed career, newest first, read off the Training Log at the
          end of the run. A dash means that value could not be read &mdash; it is left
          blank rather than guessed at, so it never skews the averages above.
        </Tooltips>
      </h3>
      <p className="text-sm text-muted-foreground mb-3">
        {runs === null
          ? "Loading…"
          : `${inRange.length} of ${runs.length} recorded career(s) in this range.`}
      </p>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs uppercase tracking-wider text-muted-foreground">
              <th className="text-left font-medium py-2 pr-4">Finished</th>
              <th className="text-right font-medium py-2 pr-4">Fans</th>
              <th className="text-right font-medium py-2 pr-4">Races</th>
              <th className="text-right font-medium py-2 pr-4">Wins</th>
              <th className="text-right font-medium py-2 pr-4">Spd</th>
              <th className="text-right font-medium py-2 pr-4">Sta</th>
              <th className="text-right font-medium py-2 pr-4">Pwr</th>
              <th className="text-right font-medium py-2 pr-4">Gut</th>
              <th className="text-right font-medium py-2 pr-4">Wit</th>
              <th className="text-right font-medium py-2 pr-4">Skill Pts</th>
              <th className="text-right font-medium py-2 pr-4">Carats</th>
              <th className="text-right font-medium py-2 pr-4">Refills</th>
              <th className="text-right font-medium py-2">Time</th>
            </tr>
          </thead>
          <tbody>
            {inRange.length === 0 && (
              <tr>
                <td colSpan={13} className="py-6 text-muted-foreground">
                  {runs === null
                    ? ""
                    : "No careers recorded in this range yet. One row is written at the end of each career."}
                </td>
              </tr>
            )}
            {[...inRange].reverse().map((r, i) => (
              <tr key={`${r.finished_at}-${i}`} className="border-t-1 border-border">
                <td className="py-2 pr-4 whitespace-nowrap">{formatWhen(r.finished_at)}</td>
                <td className="py-2 pr-4 text-right tabular-nums">{formatNumber(r.fans)}</td>
                <td className="py-2 pr-4 text-right tabular-nums">{formatNumber(r.races)}</td>
                <td className="py-2 pr-4 text-right tabular-nums">{formatNumber(r.wins)}</td>
                <td className="py-2 pr-4 text-right tabular-nums">{formatNumber(r.speed)}</td>
                <td className="py-2 pr-4 text-right tabular-nums">{formatNumber(r.stamina)}</td>
                <td className="py-2 pr-4 text-right tabular-nums">{formatNumber(r.power)}</td>
                <td className="py-2 pr-4 text-right tabular-nums">{formatNumber(r.guts)}</td>
                <td className="py-2 pr-4 text-right tabular-nums">{formatNumber(r.wit)}</td>
                <td className="py-2 pr-4 text-right tabular-nums">{formatNumber(r.skill_points)}</td>
                <td className="py-2 pr-4 text-right tabular-nums">{formatNumber(r.carats_earned)}</td>
                <td className="py-2 pr-4 text-right tabular-nums">{formatNumber(r.tp_refills)}</td>
                <td className="py-2 text-right tabular-nums whitespace-nowrap">
                  {r.duration_estimated ? (
                    <span title="The bot joined this career already in progress, so this is a typical career length rather than a measured one.">
                      ~{formatDuration(r.duration_seconds)}
                    </span>
                  ) : (
                    formatDuration(r.duration_seconds)
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
