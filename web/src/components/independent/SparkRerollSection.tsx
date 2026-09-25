import { useEffect, useState } from "react";
import { Check, Search, Sparkles, Star, X } from "lucide-react";
import type { IndependentTraining } from "@/types/independent-training.type";
import { Checkbox } from "../ui/checkbox";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "../ui/dialog";
import Tooltips from "@/components/_c/Tooltips";

type SparkReroll = IndependentTraining["spark_reroll"];
type Colour = "blue" | "pink" | "white";
type WhiteSpark = { name: string; group: string };
type Catalogue = { blue: string[]; pink: string[]; white: WhiteSpark[] };

const WHITE_GROUPS = [
  { id: "all", label: "All" },
  { id: "race", label: "Races" },
  { id: "skill", label: "Skills" },
  { id: "scenario", label: "Scenarios" },
  { id: "other", label: "Other" },
] as const;

// The colours as the game draws them, so a row reads as the spark it is about.
const SWATCH: Record<Colour, string> = {
  blue: "bg-sky-500",
  pink: "bg-pink-400",
  white: "bg-zinc-300 border border-zinc-400",
};

// The per-colour requirements are hidden for now: any career a trigger allows is
// rerolled, and which set to keep is asked in Discord. Matches COLOUR_RULES in
// core/independent_sparks.py.
const SHOW_COLOURS = false;

type Props = {
  value: SparkReroll;
  onChange: (value: SparkReroll) => void;
  // Whether a Discord bot is set up to ask which set to keep. Without one the bot has
  // nobody to ask, so it never rerolls.
  botReady: boolean;
};

export default function SparkRerollSection({ value, onChange, botReady }: Props) {
  const [catalogue, setCatalogue] = useState<Catalogue>({ blue: [], pink: [], white: [] });
  const [pickerOpen, setPickerOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [group, setGroup] = useState<(typeof WHITE_GROUPS)[number]["id"]>("all");

  useEffect(() => {
    if (!SHOW_COLOURS) return;
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch("/sparks", { cache: "no-store" });
        if (response.ok && !cancelled) setCatalogue(await response.json());
      } catch {
        // Same process as the bot; empty lists explain themselves below.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (pickerOpen) setSearch("");
  }, [pickerOpen]);

  const setColour = (colour: Colour, patch: Partial<SparkReroll[Colour]>) =>
    onChange({ ...value, [colour]: { ...value[colour], ...patch } });

  const toggleSpark = (colour: Colour, name: string) => {
    const chosen = value[colour].sparks;
    setColour(colour, {
      sparks: chosen.includes(name) ? chosen.filter((n) => n !== name) : [...chosen, name],
    });
  };

  // Blue and pink sparks come with one to three stars; a colour asks for at least this many.
  const starPicker = (colour: "blue" | "pink") => (
    <div className="flex items-center gap-2 mt-2 text-sm">
      <span className="text-muted-foreground">At least</span>
      <div className="inline-flex rounded-md border-1 border-border overflow-hidden">
        {[1, 2, 3].map((stars) => {
          // A config saved before the star minimum existed has none: that is one star,
          // which is also what the bot reads it as.
          const on = (value[colour].min_stars ?? 1) === stars;
          return (
            <button
              key={stars}
              type="button"
              aria-pressed={on}
              aria-label={`At least ${stars} star${stars > 1 ? "s" : ""}`}
              onClick={() => onChange({ ...value, [colour]: { ...value[colour], min_stars: stars } })}
              className={`px-2.5 py-1 flex gap-0.5 ${
                on ? "bg-primary text-primary-foreground" : "hover:bg-muted/50"
              }`}
            >
              {Array.from({ length: stars }, (_, i) => (
                <Star key={i} className="w-3.5 h-3.5 fill-current" />
              ))}
            </button>
          );
        })}
      </div>
    </div>
  );

  const triggered = value.at_ss_rating || value.any_rating;
  const query = search.trim().toLowerCase();
  const shownWhite = catalogue.white.filter(
    (spark) =>
      (group === "all" || spark.group === group) &&
      (!query || spark.name.toLowerCase().includes(query)),
  );

  const chips = (colour: "blue" | "pink") => (
    <div className="flex flex-wrap gap-2 mt-2">
      {catalogue[colour].map((name) => {
        const on = value[colour].sparks.includes(name);
        return (
          <button
            key={name}
            type="button"
            aria-pressed={on}
            onClick={() => toggleSpark(colour, name)}
            className={`px-3 py-1 rounded-full border text-sm transition-colors ${
              on
                ? "border-primary bg-primary/15 text-foreground"
                : "border-border text-muted-foreground hover:bg-muted/50"
            }`}
          >
            {on && <Check className="inline w-3.5 h-3.5 mr-1 -mt-0.5" />}
            {name}
          </button>
        );
      })}
    </div>
  );

  const colourRow = (colour: Colour, title: string, hint: React.ReactNode) => (
    <div className="mb-4">
      <label className="uma-label">
        <Checkbox
          checked={value[colour].required}
          onCheckedChange={() => setColour(colour, { required: !value[colour].required })}
        />
        <span className={`inline-block w-3 h-3 rounded-sm ${SWATCH[colour]}`} aria-hidden />
        Require a {title} Spark
        <Tooltips>{hint}</Tooltips>
      </label>
      <div className={value[colour].required ? "" : "disabled"}>
        {colour === "white" ? whitePicker : (
          <>
            {chips(colour)}
            {starPicker(colour)}
          </>
        )}
        {value[colour].required && value[colour].sparks.length === 0 && (
          <p className="text-xs text-muted-foreground mt-1">
            Nothing chosen yet, so this colour asks for nothing.
          </p>
        )}
      </div>
    </div>
  );

  const whitePicker = (
    <div className="mt-2">
      {value.white.sparks.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-2">
          {value.white.sparks.map((name) => (
            <span
              key={name}
              className="pl-3 pr-1.5 py-1 rounded-full border border-primary bg-primary/15
                         text-sm flex items-center gap-1"
            >
              {name}
              <button type="button" aria-label={`Remove ${name}`}
                      onClick={() => toggleSpark("white", name)}>
                <X className="w-3.5 h-3.5" />
              </button>
            </span>
          ))}
        </div>
      )}
      <Dialog open={pickerOpen} onOpenChange={setPickerOpen}>
        <DialogTrigger asChild>
          <Button type="button" variant="outline" className="uma-btn">
            <Sparkles size={16} className="mr-1" />
            Choose White Sparks
          </Button>
        </DialogTrigger>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>White Sparks</DialogTitle>
            <DialogDescription>
              Any one of the sparks chosen here meets the white requirement. Races, the
              scenario, and skills: a skill's spark can only come from a skill the trainee
              ended the career with, including the lower skill a gold one brings along.
            </DialogDescription>
          </DialogHeader>
          <div className="flex items-center gap-2 flex-wrap">
            <div className="relative grow">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-4 h-4
                                 text-muted-foreground pointer-events-none" />
              <Input
                autoFocus
                type="search"
                className="pl-8"
                placeholder="Search sparks"
                aria-label="Search sparks"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <div className="inline-flex rounded-md border-1 border-border overflow-hidden">
              {WHITE_GROUPS.map((g) => (
                <button
                  key={g.id}
                  type="button"
                  onClick={() => setGroup(g.id)}
                  className={`px-3 py-1.5 text-sm ${
                    group === g.id ? "bg-primary text-primary-foreground" : "hover:bg-muted/50"
                  }`}
                >
                  {g.label}
                </button>
              ))}
            </div>
          </div>
          <p className="text-sm text-muted-foreground tabular-nums">
            {shownWhite.length} of {catalogue.white.length} &middot;{" "}
            {value.white.sparks.length} chosen
          </p>
          {shownWhite.length === 0 ? (
            <p className="text-sm text-muted-foreground py-6">
              {catalogue.white.length === 0
                ? "No spark list loaded. It comes from data/sparks.json."
                : `No spark matches “${search}”.`}
            </p>
          ) : (
            <div className="grid sm:grid-cols-2 grid-cols-1 gap-1.5 max-h-[55vh] overflow-y-auto">
              {shownWhite.map((spark) => {
                const on = value.white.sparks.includes(spark.name);
                return (
                  <button
                    key={spark.name}
                    type="button"
                    aria-pressed={on}
                    onClick={() => toggleSpark("white", spark.name)}
                    className={`flex items-center gap-2 px-3 py-2 rounded-md border text-left
                                text-sm transition-colors ${
                                  on
                                    ? "border-primary bg-primary/10"
                                    : "border-border hover:bg-muted/50"
                                }`}
                  >
                    <span className="grow">{spark.name}</span>
                    <span className="text-xs text-muted-foreground capitalize">
                      {spark.group}
                    </span>
                    {on && <Check className="shrink-0 w-4 h-4 text-primary" />}
                  </button>
                );
              })}
            </div>
          )}
          <DialogFooter>
            <Button type="button" onClick={() => setPickerOpen(false)}>
              Done
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );

  return (
    <>
      <h3 className="text-xl font-semibold mt-6 mb-2 flex items-center gap-2">
        Spark Reroll
        <Tooltips>
          After a career the game grants sparks, and they can be rerolled once for 30 TP
          before choosing which set to keep. The bot rerolls every career a trigger below
          allows, then asks you in Discord which set to keep.
        </Tooltips>
      </h3>

      <div className="grid lg:grid-cols-2 grid-cols-1 gap-2 mb-4">
        <label className="uma-label">
          <Checkbox
            checked={value.at_ss_rating}
            onCheckedChange={() => onChange({ ...value, at_ss_rating: !value.at_ss_rating })}
          />
          Reroll at SS Rating or Higher
          <Tooltips>
            Only for careers that rate SS (17,500) or better, read off the Career Rank
            screen straight after Complete Career.
          </Tooltips>
        </label>
        <label className="uma-label">
          <Checkbox
            checked={value.any_rating}
            onCheckedChange={() => onChange({ ...value, any_rating: !value.any_rating })}
          />
          Reroll at Any Rating
          <Tooltips>
            Overrides the rating: every career is rerolled, whatever it rated.
          </Tooltips>
        </label>
      </div>

      <div className={triggered ? "" : "disabled"}>
        <label className="uma-label mb-3">
          <Checkbox
            checked={value.ask_first ?? false}
            onCheckedChange={() => onChange({ ...value, ask_first: !value.ask_first })}
          />
          Ask Before Rerolling
          <Tooltips>
            Instead of rerolling on its own, the bot sends you the rating, the blue and
            pink sparks, the white sparks that match your priority skills, then every
            white spark with the total, and rerolls only if you answer Reroll. The triggers above
            still decide which careers you are asked about.
          </Tooltips>
        </label>
        {!triggered && (
          <p className="text-sm text-muted-foreground mb-3">
            Neither trigger is on, so the sparks are never rerolled.
          </p>
        )}
        {triggered && !botReady && (
          <p className="text-sm text-destructive mb-3">
            After a reroll you choose the set to keep in Discord, which needs the Mirako
            Bot linked under Discord Notifications. Until it is linked the sparks are
            kept as granted.
          </p>
        )}
        {SHOW_COLOURS && (<>
        {colourRow("blue", "Blue", "A stat spark: one of the five stats.")}
        {colourRow(
          "pink",
          "Pink",
          <>
            An aptitude spark. The trainee can only be granted one for an aptitude it has
            at A or better. A chosen aptitude the trainee has below A is left out for that
            career, and if none of the chosen ones is possible, pink is not rerolled for.
          </>,
        )}
        <p className="text-xs text-muted-foreground -mt-3 mb-4">
          Only aptitudes the trainee has at A or better can come up as a pink spark.
        </p>
        {colourRow(
          "white",
          "White",
          <>
            Race, skill and scenario sparks; a career usually grants several. A skill's
            spark only comes from a skill the trainee holds &mdash; one bought, or brought
            along by one bought, as Superstan brings Uma Stan. Chosen skills the career did
            not end with are left out, and if none is possible, white is not rerolled for.
          </>,
        )}
        </>)}
      </div>
    </>
  );
}
