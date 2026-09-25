import { useEffect, useState } from "react";
import { AlertTriangle, Bot, Check, Images, Search, X } from "lucide-react";
import type { Config, UpdateConfigType } from "@/types";
import { Input } from "../ui/input";
import { Checkbox } from "../ui/checkbox";
import { Button } from "../ui/button";
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../ui/select";
import { cardMatches } from "@/lib/borrow-search";
import SparkRerollSection from "./SparkRerollSection";

// A card is identified by the title the game prints for it, not by its artwork, so
// `file` here is only a thumbnail for this picker and may be empty.
type BorrowCard = {
  title: string;
  character: string;
  rarity: string;
  type: string;
  file: string;
};

type Props = {
  config: Config;
  updateConfig: UpdateConfigType;
};

export default function IndependentSection({ config, updateConfig }: Props) {
  const independent = config.independent_training;
  const webhook = config.webhook;

  const updateWebhook = (patch: Partial<typeof webhook>) =>
    updateConfig("webhook", { ...webhook, ...patch });

  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; detail: string } | null>(null);

  // The URL is sent with the request rather than read from the saved config, so the
  // button works on one just pasted in -- which is when it is most wanted.
  const sendTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await fetch("/webhook/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: webhook.url }),
      });
      const data = await res.json();
      setTestResult({ ok: data.status === "success", detail: data.detail });
    } catch {
      setTestResult({ ok: false, detail: "Could not reach the bot's server." });
    } finally {
      setTesting(false);
    }
  };

  const [botTesting, setBotTesting] = useState(false);
  const [botResult, setBotResult] = useState<{ ok: boolean; detail: string } | null>(null);
  // What is typed, not what is saved, as with the webhook test.
  // The invite link, built from the token. A bot token's first part is its application ID
  // in base64, so the link needs nothing else -- which spares the Developer Portal's URL
  // Generator, a page it is easy to come away from with nothing but the ID. The
  // permissions are View Channels, Send Messages, Attach Files, Add Reactions and Read
  // Message History, and nothing more.
  const appId = (() => {
    try {
      const head = webhook.bot_token.trim().split(".")[0];
      const padded = head.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((head.length + 3) % 4);
      const id = atob(padded);
      return /^\d{15,22}$/.test(id) ? id : "";
    } catch {
      return "";
    }
  })();

  // Where the spark question goes, and whether that is filled in.
  const toDm = webhook.choice_target === "dm";
  // For DMs the app is added to the person's own account (a user install), after which it
  // can DM them with no server in common; for a channel it joins the server instead.
  const inviteLink = !appId
    ? ""
    : toDm
      ? `https://discord.com/oauth2/authorize?client_id=${appId}&integration_type=1&scope=applications.commands`
      : `https://discord.com/oauth2/authorize?client_id=${appId}&scope=bot&permissions=101440`;
  const botReady = Boolean(
    webhook.bot_token && (toDm ? webhook.choice_user_id : webhook.choice_channel_id),
  );
  // DMs set up take the notifications too, in place of the webhook (utils/webhook.py).
  const dmActive = toDm && botReady;

  const testBot = async () => {
    setBotTesting(true);
    setBotResult(null);
    try {
      const res = await fetch("/discord/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          toDm
            ? { token: webhook.bot_token, user: webhook.choice_user_id }
            : { token: webhook.bot_token, channel: webhook.choice_channel_id },
        ),
      });
      const data = await res.json();
      setBotResult({ ok: data.status === "success", detail: data.detail });
    } catch {
      setBotResult({ ok: false, detail: "Could not reach the bot's server." });
    } finally {
      setBotTesting(false);
    }
  };

  const teamTrials = config.team_trials;
  const updateTeamTrials = (patch: Partial<typeof teamTrials>) =>
    updateConfig("team_trials", { ...teamTrials, ...patch });

  const update = (patch: Partial<typeof independent>) =>
    updateConfig("independent_training", { ...independent, ...patch });

  // Whatever templates are on disk. Listed by the server rather than hardcoded, so
  // dropping a new crop into assets/independent/borrow makes it selectable with no code
  // change -- which is the whole point of a picker over a path to type.
  const [cards, setCards] = useState<BorrowCard[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  // Cleared each time the picker opens, so it never reopens on a stale, empty-looking
  // filter from last time.
  const [search, setSearch] = useState("");
  useEffect(() => {
    if (pickerOpen) setSearch("");
  }, [pickerOpen]);
  const shownCards = cards.filter((card) => cardMatches(card, search));

  // Fetched on mount, not only when the picker opens. The chosen card's own row draws
  // its artwork and character out of this same list, so gating the fetch on the picker
  // left that row blank on every fresh page load -- until you happened to open the
  // picker once, which is not something you do to look at a card you already chose. The
  // refetch on open is what keeps a crop dropped into the folder mid-session visible.
  useEffect(() => {
    if (!pickerOpen && cards.length > 0) return;
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch("/borrow/cards", { cache: "no-store" });
        if (response.ok && !cancelled) setCards((await response.json()).cards ?? []);
      } catch {
        // Same process as the bot; an empty list renders its own explanation below.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [pickerOpen, cards.length]);

  // One card, chosen rather than accumulated. Only one is ever borrowed, so a list of
  // them only ever meant "any of these will do, in this order" -- and stored as an array
  // it still can: the config loader and _borrow_templates both take a list or a string,
  // so a hand-edited config with several entries keeps working as a fallback chain.
  const chosenCard = independent.borrow_cards[0] ?? null;
  const chooseCard = (title: string) => {
    update({ borrow_cards: chosenCard === title ? [] : [title] });
  };
  // A card chosen before it was added to the library still has to render, so fall back
  // to showing the stored title rather than dropping the selection on the floor.
  const chosenEntry = cards.find((c) => c.title === chosenCard) ?? null;


  return (
    <div className="section-card">
      <h2 className="text-3xl font-semibold mb-4 flex items-center gap-3">
        <Bot className="text-primary" />
        Automation
      </h2>

      <h3 className="text-xl font-semibold mb-2 flex items-center gap-2">
        Independent Training
      </h3>

      <div className="grid lg:grid-cols-3 grid-cols-1 gap-2">
        <label className="uma-label">
          <span>Careers To Run</span>
          <Tooltips>Stop after this many careers. 0 keeps going until you stop it.</Tooltips>
          <Input
            className="w-20"
            type="number"
            min={0}
            value={independent.max_runs}
            onChange={(e) => update({ max_runs: e.target.valueAsNumber || 0 })}
          />
        </label>

        <label className={`uma-label ${independent.max_runs ? "" : "disabled"}`}>
          <span>After The Last Career</span>
          <Tooltips>
            What to do once the number above is reached. Stopping is the old behaviour.
            Carrying on leaves the daily collections, the daily races and Team Trials
            running on their own timers, without starting another fifty-minute career.
            Nothing to set while Careers To Run is 0, since the bot never finishes.
          </Tooltips>
          <Select
            value={independent.after_max_runs}
            onValueChange={(v) => update({ after_max_runs: v as "stop" | "dailies" })}
          >
            <SelectTrigger className="w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="stop">Stop the bot</SelectItem>
              <SelectItem value="dailies">Keep doing dailies</SelectItem>
            </SelectContent>
          </Select>
        </label>

        <label className="uma-label">
          <span>Borrow Warning Every</span>
          <Tooltips>
            How many refreshes of the borrow list to go through before saying so in the
            log. A card that is not in anyone's support list will never appear, and the
            bot keeps refreshing -- this is how often it tells you that is happening.
          </Tooltips>
          <Input
            className="w-20"
            type="number"
            min={1}
            value={independent.borrow_warn_every_refreshes}
            onChange={(e) =>
              update({ borrow_warn_every_refreshes: e.target.valueAsNumber || 10 })
            }
          />
        </label>

        <label className="uma-label">
          <span>Training Minutes</span>
          <Tooltips>
            How long a career takes, used only as a fallback. The bot reads the on-screen
            countdown, so this matters just when that cannot be read.
          </Tooltips>
          <Input
            className="w-20"
            type="number"
            min={1}
            value={independent.training_minutes}
            onChange={(e) => update({ training_minutes: e.target.valueAsNumber || 50 })}
          />
        </label>

        <label className="uma-label">
          <span>Poll Seconds</span>
          <Tooltips>How often to check whether the career has finished.</Tooltips>
          <Input
            className="w-20"
            type="number"
            min={15}
            value={independent.wait_poll_seconds}
            onChange={(e) => update({ wait_poll_seconds: e.target.valueAsNumber || 60 })}
          />
        </label>

        <label className="uma-label">
          <span>Training Focus</span>
          <Tooltips>
            The Balanced / Stamina / Sprint choice on the Final Confirmation screen.
            "Leave as default" keeps whatever is already selected. Otherwise the bot
            reads which one is lit and only clicks when it differs, so setting this to
            what you already use costs nothing.
          </Tooltips>
          <select
            className="border-1 border-border rounded-md px-2 py-1 bg-transparent dark:bg-input/30"
            value={independent.training_focus}
            onChange={(e) =>
              update({ training_focus: e.target.value as typeof independent.training_focus })
            }
          >
            <option value="default">Leave as default</option>
            <option value="balanced">Balanced</option>
            <option value="stamina">Stamina</option>
            <option value="sprint">Sprint</option>
          </select>
        </label>

        <label className="uma-label">
          <span>Racing Style</span>
          <Tooltips>
            Set on the Final Confirmation screen, just before the career starts. "Leave
            as default" keeps whatever style the trainee already has and skips the
            dialog entirely. The game shows an aptitude grade for each style &mdash; a
            trainee with a G in Front will do badly in it, so this is worth matching to
            the trainee rather than setting once and forgetting.
          </Tooltips>
          <select
            className="border-1 border-border rounded-md px-2 py-1 bg-transparent dark:bg-input/30"
            value={independent.racing_style}
            onChange={(e) =>
              update({ racing_style: e.target.value as typeof independent.racing_style })
            }
          >
            <option value="default">Leave as default</option>
            <option value="front">Front</option>
            <option value="pace">Pace</option>
            <option value="late">Late</option>
            <option value="end">End</option>
          </select>
        </label>

        <label className="uma-label col-span-3">
          <Checkbox
            checked={independent.spend_leftover_points}
            onCheckedChange={() =>
              update({ spend_leftover_points: !independent.spend_leftover_points })
            }
          />
          Spend Leftover Skill Points
          <Tooltips>
            Once your priority list is bought, put whatever is left into skills that are
            not on it. Points do not survive the end of a career, so an unspent balance
            is simply lost.
          </Tooltips>
        </label>

        <label className={`uma-label col-span-3 ${independent.spend_leftover_points ? "" : "disabled"}`}>
          <Checkbox
            checked={independent.maximize_rating}
            disabled={!independent.spend_leftover_points}
            onCheckedChange={() =>
              update({ maximize_rating: !independent.maximize_rating })
            }
          />
          Maximise Rating
          <Tooltips>
            Work out which combination of leftover skills is worth the most rating for
            the points available, instead of walking the list in some order. Each skill
            is scored against this trainee&rsquo;s aptitudes, which the bot reads off the
            Complete Career screen at the end of every career &mdash; so a Long skill
            counts for more on a trainee with Long A than on one with Long G. It also
            spots when a cheap gold skill hands over a white one you were already paying
            for, and takes the refund.
          </Tooltips>
        </label>

        {!independent.maximize_rating && (
          <label className={`uma-label col-span-3 ${independent.spend_leftover_points ? "" : "disabled"}`}>
            <span>Leftover Order</span>
            <Tooltips>
              Which extras to reach first when not maximising rating. &ldquo;Bottom of the
              list&rdquo; takes the end of the game&rsquo;s list upwards, the direction the
              bot already walks. &ldquo;Biggest discount&rdquo; takes the most heavily
              discounted first, which buys the most skills for the points &mdash; the
              discount is worked out from the price on screen against the skill&rsquo;s
              base cost, so it costs nothing extra to read.
            </Tooltips>
            <select
              className="border-1 border-border rounded-md px-2 py-1 bg-transparent dark:bg-input/30"
              value={independent.leftover_strategy}
              disabled={!independent.spend_leftover_points}
              onChange={(e) =>
                update({ leftover_strategy: e.target.value as typeof independent.leftover_strategy })
              }
            >
              <option value="bottom_up">Bottom of the list first</option>
              <option value="best_value">Biggest discount first</option>
            </select>
          </label>
        )}
      </div>

      <h3 className="text-xl font-semibold mt-6 mb-2 flex items-center gap-2">
        Scenario
      </h3>

      <label className="uma-label">
        <span>Career Scenario</span>
        <Tooltips>
          Picked on the Scenario Select screen. "Leave as selected" starts whichever
          scenario the game already has. Otherwise the bot reads the scenario's
          description and turns the carousel until the chosen one is showing; if it
          cannot find it, it stops rather than start a career in a different scenario.
        </Tooltips>
        <select
          className="border-1 border-border rounded-md px-2 py-1 bg-transparent dark:bg-input/30"
          value={independent.scenario}
          onChange={(e) =>
            update({ scenario: e.target.value as typeof independent.scenario })
          }
        >
          <option value="default">Leave as selected</option>
          <option value="ura_finale">URA Finale</option>
          <option value="unity_cup">Unity Cup</option>
          <option value="trackblazer">Trackblazer</option>
          <option value="grand_concert">Our Grand Concert</option>
        </select>
      </label>

      <h3 className="text-xl font-semibold mt-6 mb-2 flex items-center gap-2">
        Support Deck
      </h3>

      <div className="grid lg:grid-cols-3 grid-cols-1 gap-2">
        <label className="uma-label">
          <span>Deck</span>
          <Tooltips>
            Picked on the Support Formation screen by its position, so a deck you have
            renamed is still found. "Leave as selected" uses whichever deck is showing.
          </Tooltips>
          <select
            className="border-1 border-border rounded-md px-2 py-1 bg-transparent dark:bg-input/30"
            value={independent.deck}
            disabled={independent.deck_name.trim() !== ""}
            onChange={(e) => update({ deck: Number(e.target.value) })}
          >
            <option value={0}>Leave as selected</option>
            {Array.from({ length: 10 }, (_, index) => (
              <option key={index + 1} value={index + 1}>
                Deck {index + 1}
              </option>
            ))}
          </select>
        </label>

        <label className="uma-label">
          <span>Custom Deck Name</span>
          <Tooltips>
            The name you gave a deck in the game. When filled in, this is used instead of
            the Deck dropdown: the bot reads each deck&apos;s name and turns until it
            matches, ignoring case, spaces and punctuation. If no deck has this name it
            stops rather than start with a different deck.
          </Tooltips>
          <Input
            className="w-40"
            type="text"
            maxLength={10}
            placeholder="e.g. Speed Team"
            value={independent.deck_name}
            onChange={(e) => update({ deck_name: e.target.value })}
          />
        </label>
      </div>

      <h3 className="text-xl font-semibold mt-6 mb-2 flex items-center gap-2">
        Race Agenda
      </h3>

      <div className="grid lg:grid-cols-3 grid-cols-1 gap-2">
        <label className="uma-label">
          <span>Agenda</span>
          <Tooltips>
            Loaded from My Agendas on the Final Confirmation screen, picked by its position
            in that list &mdash; so an agenda you have renamed is still found. Agenda 1 is
            the top of the list, which is what the bot has always loaded.
          </Tooltips>
          <select
            className="border-1 border-border rounded-md px-2 py-1 bg-transparent dark:bg-input/30"
            value={independent.agenda_slot}
            disabled={independent.agenda_name.trim() !== ""}
            onChange={(e) => update({ agenda_slot: Number(e.target.value) })}
          >
            {Array.from({ length: 8 }, (_, index) => (
              <option key={index + 1} value={index + 1}>
                {index === 0 ? "Agenda 1 (top of the list)" : `Agenda ${index + 1}`}
              </option>
            ))}
          </select>
        </label>

        <label className="uma-label">
          <span>Custom Agenda Name</span>
          <Tooltips>
            The name you gave an agenda in the game. When filled in, this is used instead
            of the Agenda dropdown: the bot reads each agenda&apos;s name from the top of
            the list down and loads the first that matches, ignoring case, spaces and
            punctuation. Two agendas can share a name, and the higher one wins. If none
            has this name it stops rather than start with a different agenda.
          </Tooltips>
          <Input
            className="w-40"
            type="text"
            maxLength={20}
            placeholder="e.g. CROWN"
            value={independent.agenda_name}
            onChange={(e) => update({ agenda_name: e.target.value })}
          />
        </label>
      </div>

      <h3 className="text-xl font-semibold mt-6 mb-2 flex items-center gap-2">
        Borrow Card
        <Tooltips>
          A career will not start with an empty friend slot, so one card is required.
          The card is found by the name the Borrow Card list prints for it, so nothing
          drawn over the artwork &mdash; a Scenario Link banner, a Duplicate Support
          flag &mdash; can hide it. Only cards at max limit break are taken, which is
          checked separately by counting the pips. If the card is not among the friends
          on offer, the bot refreshes the list until it is.
        </Tooltips>
      </h3>

      <div>
        {chosenCard === null ? (
          <p className="text-sm text-muted-foreground mb-3">
            No card chosen &mdash; the bot will stop rather than start a career without
            one.
          </p>
        ) : (
          <div className="px-2 py-1.5 mb-3 rounded-md flex gap-3 items-center border-1
                          border-border bg-transparent dark:bg-input/30">
            {chosenEntry?.file ? (
              <img
                src={`/borrow/image/${chosenEntry.file}`}
                alt=""
                className="h-9 rounded-sm border border-border/60"
              />
            ) : (
              <span className="h-9 w-7 rounded-sm border border-dashed border-border/60" />
            )}
            <span className="grow text-sm">
              {chosenCard}
              {chosenEntry?.character && (
                <span className="text-muted-foreground"> &middot; {chosenEntry.character}</span>
              )}
            </span>
            <button
              type="button"
              aria-label={`Remove ${chosenCard}`}
              onClick={() => update({ borrow_cards: [] })}
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        )}

        {independent.borrow_cards.length > 1 && (
          <p className="text-sm text-muted-foreground mb-3">
            {independent.borrow_cards.length - 1} further card
            {independent.borrow_cards.length > 2 ? "s are" : " is"} set in the config file
            and still tried in order if this one is not on offer. Choosing below replaces
            the lot with one.
          </p>
        )}

        <Dialog open={pickerOpen} onOpenChange={setPickerOpen}>
          <DialogTrigger asChild>
            <Button type="button" variant="outline" className="uma-btn">
              <Images size={16} className="mr-1" />
              Choose Card
            </Button>
          </DialogTrigger>
          <DialogContent className="sm:max-w-2xl">
            <DialogHeader>
              <DialogTitle>Borrow Card</DialogTitle>
              <DialogDescription>
                Every SSR and SR card in <code>data/borrow_cards.json</code>. Pick the
                one to borrow; only one card is ever borrowed for a career. New cards
                come from rerunning <code>devtools/scrape_support_cards.py --images</code>.
              </DialogDescription>
            </DialogHeader>

            {cards.length === 0 ? (
              <p className="text-sm text-muted-foreground py-6">
                No cards in the library. Add one to
                <code className="mx-1">data/borrow_cards.json</code>
                and it will appear here.
              </p>
            ) : (
              <>
              <div className="flex items-center gap-2">
                <div className="relative grow">
                  <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-4 h-4
                                     text-muted-foreground pointer-events-none" />
                  <Input
                    autoFocus
                    type="search"
                    className="pl-8"
                    placeholder="Search by title, character, rarity or type"
                    aria-label="Search cards"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </div>
                <span className="text-sm text-muted-foreground tabular-nums shrink-0">
                  {shownCards.length} of {cards.length}
                </span>
              </div>
              {shownCards.length === 0 ? (
                <p className="text-sm text-muted-foreground py-6">
                  No card matches &ldquo;{search}&rdquo;.
                </p>
              ) : (
              <div className="grid sm:grid-cols-2 grid-cols-1 gap-2 max-h-[55vh] overflow-y-auto">
                {shownCards.map((card) => {
                  const isChosen = chosenCard === card.title;
                  return (
                    <button
                      key={card.title}
                      type="button"
                      aria-pressed={isChosen}
                      onClick={() => chooseCard(card.title)}
                      className={`flex items-center gap-3 p-2 rounded-md border text-left
                                  transition-colors ${
                                    isChosen
                                      ? "border-primary bg-primary/10"
                                      : "border-border hover:bg-muted/50"
                                  }`}
                    >
                      {card.file ? (
                        <img
                          src={`/borrow/image/${card.file}`}
                          alt=""
                          loading="lazy"
                          className="h-12 w-9 object-cover rounded-sm border border-border/60"
                        />
                      ) : (
                        <span className="h-12 w-9 rounded-sm border border-dashed
                                         border-border/60" />
                      )}
                      <span className="grow text-sm">
                        {card.title}
                        {(card.character || card.rarity) && (
                          <span className="block text-xs text-muted-foreground">
                            {[card.character, [card.rarity, card.type].filter(Boolean).join(" ")]
                              .filter(Boolean)
                              .join(" · ")}
                          </span>
                        )}
                      </span>
                      {isChosen && (
                        <Check className="shrink-0 w-5 h-5 text-primary" />
                      )}
                    </button>
                  );
                })}
              </div>
              )}
              </>
            )}

            <DialogFooter>
              <Button type="button" onClick={() => setPickerOpen(false)}>
                Done
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      <SparkRerollSection
        value={independent.spark_reroll}
        onChange={(spark_reroll) => update({ spark_reroll })}
        botReady={botReady}
      />

      <h3 className="text-xl font-semibold mt-6 mb-2 flex items-center gap-2">
        Discord Notifications
        <Tooltips>
          Sends a message to a Discord channel as things happen &mdash; the point of
          which is a run you are not watching. Paste a channel's webhook URL to switch it
          on; leave it empty and nothing is sent.
        </Tooltips>
      </h3>

      <label className="uma-label col-span-3">
        <span className="whitespace-nowrap">Webhook URL</span>
        <Input
          type="password"
          className="grow"
          placeholder="https://discord.com/api/webhooks/..."
          value={webhook.url}
          onChange={(e) => {
            updateWebhook({ url: e.target.value });
            setTestResult(null);
          }}
        />
        <Button
          type="button"
          variant="outline"
          disabled={!webhook.url || testing}
          onClick={sendTest}
        >
          {testing ? "Sending…" : "Send Test"}
        </Button>
      </label>
      {testResult && (
        <p
          className={`text-sm mt-2 flex items-start gap-2 ${
            testResult.ok ? "text-primary" : "text-destructive"
          }`}
        >
          {testResult.ok ? (
            <Check className="w-4 h-4 mt-0.5 shrink-0" />
          ) : (
            <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          )}
          {testResult.detail}
        </p>
      )}
      <p className="text-xs text-muted-foreground mt-1 mb-3">
        In Discord: Server Settings &rarr; Integrations &rarr; Webhooks &rarr; New
        Webhook, then Copy Webhook URL. Treat it like a password &mdash; anyone with it
        can post to that channel.
      </p>
      {dmActive && (
        <p className="text-sm text-primary mb-3">
          Spark Choice Bot is sending to your DMs, so these messages go there too, in place
          of the webhook.
        </p>
      )}

      <div className={`grid lg:grid-cols-3 grid-cols-1 gap-2 ${webhook.url || dmActive ? "" : "disabled"}`}>
        <label className="uma-label">
          <Checkbox
            checked={webhook.career_summary_enabled}
            onCheckedChange={() =>
              updateWebhook({ career_summary_enabled: !webhook.career_summary_enabled })
            }
          />
          Career Results
          <Tooltips>
            One message per finished career: fans, race record, final stats and how long
            it took, read off the same screen the Statistics tab records.
          </Tooltips>
        </label>
        <label className="uma-label">
          <Checkbox
            checked={webhook.recovery_enabled}
            onCheckedChange={() =>
              updateWebhook({ recovery_enabled: !webhook.recovery_enabled })
            }
          />
          Interruptions
          <Tooltips>
            Sent when a connection drops or the daily server reset boots the game out,
            and the bot starts climbing back. It recovers on its own &mdash; the useful
            signal is that it is happening, and how often.
          </Tooltips>
        </label>
        <label className="uma-label">
          <Checkbox
            checked={webhook.skills_enabled}
            onCheckedChange={() =>
              updateWebhook({ skills_enabled: !webhook.skills_enabled })
            }
          />
          Skills Bought
          <Tooltips>
            The list of skills purchased at the end of each career.
          </Tooltips>
        </label>
      </div>

      <h4 className="text-base font-semibold mt-5 mb-2 flex items-center gap-2">
        Spark Choice Bot
        <Tooltips>
          After a spark reroll you choose which set to keep, and the question is asked in
          Discord: both sets are posted as pictures and you react 1 or 2. A webhook can
          only post, so this needs a Discord bot. In the Developer Portal: New
          Application, then Bot, Reset Token and copy it here; then OAuth2, URL
          Generator, tick "bot" and the permissions View Channel, Send Messages, Attach
          Files, Add Reactions and Read Message History, and open the link to invite it to
          your server. The bot waits for your answer as long as it takes.
        </Tooltips>
      </h4>
      {!botReady && (
        <ol className="text-sm text-muted-foreground list-decimal pl-5 mb-3 space-y-1">
          <li>
            Open the{" "}
            <a className="underline" href="https://discord.com/developers/applications"
               target="_blank" rel="noreferrer">
              Discord Developer Portal
            </a>{" "}
            and press <b>New Application</b>. Any name will do.
          </li>
          <li>
            On its <b>Bot</b> page, press <b>Reset Token</b> and paste the token into Bot
            Token below.
          </li>
          <li>
            {inviteLink ? (
              <>
                Open{" "}
                <a className="underline" href={inviteLink} target="_blank" rel="noreferrer">
                  this {toDm ? "install" : "invite"} link
                </a>{" "}
                {toDm ? (
                  <>
                    and choose <b>Add to My Apps</b>. That lets it DM you with no server in
                    common. If Discord does not offer it, turn on <b>User Install</b> on the
                    app's Installation page first.
                  </>
                ) : (
                  <>
                    and add the bot to your server. It asks for exactly the permissions the
                    bot uses: View Channels, Send Messages, Attach Files, Add Reactions and
                    Read Message History.
                  </>
                )}
              </>
            ) : (
              <>Paste the token first: the invite link for step 3 appears here.</>
            )}
          </li>
          <li>
            In Discord, turn on <b>Developer Mode</b> (User Settings &rarr; Advanced), then{" "}
            {toDm ? (
              <>
                right-click your own name and <b>Copy User ID</b>. Paste it below.
              </>
            ) : (
              <>
                right-click the channel to use and <b>Copy Channel ID</b>. Paste it below.
              </>
            )}
          </li>
          <li>
            Press <b>Test</b>. A message from the bot {toDm ? "in your DMs" : "in that channel"}{" "}
            means it is ready.
          </li>
        </ol>
      )}
      <div className="grid lg:grid-cols-2 grid-cols-1 gap-2">
        <label className="uma-label">
          <span className="whitespace-nowrap">Bot Token</span>
          <Input
            type="password"
            className="grow"
            placeholder="From the Bot page"
            value={webhook.bot_token}
            onChange={(e) => {
              updateWebhook({ bot_token: e.target.value });
              setBotResult(null);
            }}
          />
        </label>
        <label className="uma-label">
          <span className="whitespace-nowrap">Send To</span>
          <div className="inline-flex rounded-md border-1 border-border overflow-hidden">
            {(["dm", "channel"] as const).map((target) => (
              <button
                key={target}
                type="button"
                aria-pressed={(webhook.choice_target ?? "channel") === target}
                onClick={() => {
                  updateWebhook({ choice_target: target });
                  setBotResult(null);
                }}
                className={`px-3 py-1.5 text-sm ${
                  (webhook.choice_target ?? "channel") === target
                    ? "bg-primary text-primary-foreground"
                    : "hover:bg-muted/50"
                }`}
              >
                {target === "dm" ? "My DMs" : "A Channel"}
              </button>
            ))}
          </div>
        </label>
        <label className="uma-label lg:col-span-2">
          <span className="whitespace-nowrap">{toDm ? "Your User ID" : "Channel ID"}</span>
          <Input
            className="grow"
            placeholder={
              toDm
                ? "Right-click your own name, Copy User ID"
                : "Right-click the channel, Copy Channel ID"
            }
            value={(toDm ? webhook.choice_user_id : webhook.choice_channel_id) ?? ""}
            onChange={(e) => {
              updateWebhook(
                toDm
                  ? { choice_user_id: e.target.value.trim() }
                  : { choice_channel_id: e.target.value.trim() },
              );
              setBotResult(null);
            }}
          />
          <Button
            type="button"
            variant="outline"
            disabled={!botReady || botTesting}
            onClick={testBot}
          >
            {botTesting ? "Testing…" : "Test"}
          </Button>
        </label>
      </div>
      {botResult && (
        <p
          className={`text-sm mt-2 flex items-start gap-2 ${
            botResult.ok ? "text-primary" : "text-destructive"
          }`}
        >
          {botResult.ok ? (
            <Check className="w-4 h-4 mt-0.5 shrink-0" />
          ) : (
            <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          )}
          {botResult.detail}
        </p>
      )}

      <h3 className="text-xl font-semibold mt-6 mb-2 flex items-center gap-2">
        TP Refill
        <Tooltips>
          When a career costs more TP than you have, recover 30 TP at a time from the
          game's Recover TP screen and carry on. Only Carats and Toughness 30 are ever
          spent &mdash; the Handmade Chocolates in that list are left alone.
        </Tooltips>
      </h3>

      <div className="grid lg:grid-cols-3 grid-cols-1 gap-2">
        <label className="uma-label col-span-3">
          <Checkbox
            checked={independent.tp_refill_enabled}
            onCheckedChange={() =>
              update({ tp_refill_enabled: !independent.tp_refill_enabled })
            }
          />
          Refill TP Automatically
          <Tooltips>
            With this off, the bot stops when a career costs more TP than you have.
          </Tooltips>
        </label>

        <label className={`uma-label col-span-3 ${independent.tp_refill_enabled ? "" : "disabled"}`}>
          <span>Spend</span>
          <Tooltips>
            Carats are premium currency; Toughness 30 is the free recovery item the game
            hands out. Each is worth 30 TP, and carats cost 10 per use. Whichever is
            chosen, the carat floor below is never crossed.
          </Tooltips>
          <select
            className="border-1 border-border rounded-md px-2 py-1 bg-transparent dark:bg-input/30"
            value={independent.tp_refill_strategy}
            disabled={!independent.tp_refill_enabled}
            onChange={(e) =>
              update({
                tp_refill_strategy: e.target
                  .value as typeof independent.tp_refill_strategy,
              })
            }
          >
            <option value="toughness_only">Toughness 30 only, never carats</option>
            <option value="toughness_first">Toughness 30 first, then carats</option>
            <option value="carats_first">Carats first, save the items</option>
          </select>
        </label>

        <label className={`uma-label ${independent.tp_refill_enabled ? "" : "disabled"}`}>
          <span>Max Refills Per Session</span>
          <Tooltips>
            How many 30 TP top-ups to allow before stopping instead. Counted per use, so
            a career needing 60 TP spends two of them. 0 means none, which leaves the
            bot stopping on empty TP as though refill were off.
          </Tooltips>
          <Input
            className="w-20"
            type="number"
            min={0}
            value={independent.tp_refill_max_per_session}
            disabled={!independent.tp_refill_enabled}
            onChange={(e) =>
              update({ tp_refill_max_per_session: e.target.valueAsNumber || 0 })
            }
          />
        </label>

        <label className={`uma-label ${independent.tp_refill_enabled ? "" : "disabled"}`}>
          <span>Keep At Least This Many Carats</span>
          <Tooltips>
            A floor the bot will not spend past. If a 10 carat refill would take you
            below it, the carats are left alone. Ignored when spending Toughness 30.
          </Tooltips>
          <Input
            className="w-24"
            type="number"
            min={0}
            value={independent.tp_refill_min_carats_remaining}
            disabled={!independent.tp_refill_enabled}
            onChange={(e) =>
              update({ tp_refill_min_carats_remaining: e.target.valueAsNumber || 0 })
            }
          />
        </label>
      </div>

      <h3 className="text-xl font-semibold mt-6 mb-2 flex items-center gap-2">
        Team Trials
        <Tooltips>
          Run Team Trials between careers. Each race spends one RP charge, which refills
          one every two hours up to five &mdash; so a career leaves roughly enough for one
          race. The bot always picks the top opponent, races with quick mode on, and
          stops when the game says there is not enough RP.
        </Tooltips>
      </h3>

      <div className="grid lg:grid-cols-3 grid-cols-1 gap-2">
        <label className="uma-label col-span-3">
          <Checkbox
            checked={teamTrials.enabled}
            onCheckedChange={() => updateTeamTrials({ enabled: !teamTrials.enabled })}
          />
          Run Team Trials
          <Tooltips>
            Off by default, because it spends a resource &mdash; a bot that quietly starts
            doing that because it was updated is a bot nobody asked for.
          </Tooltips>
        </label>

        <label className={`uma-label ${teamTrials.enabled ? "" : "disabled"}`}>
          <span>Keep Charges</span>
          <Tooltips>
            Stop once this many RP charges are left rather than draining the bar, so there
            is something in hand to play with. 0 spends everything.
          </Tooltips>
          <Input
            className="w-20"
            type="number"
            min={0}
            max={5}
            disabled={!teamTrials.enabled}
            value={teamTrials.keep_charges}
            onChange={(e) =>
              updateTeamTrials({ keep_charges: Math.max(0, Math.min(5, Number(e.target.value) || 0)) })
            }
          />
        </label>

        <label className="uma-label col-span-3">
          <Checkbox
            checked={teamTrials.prioritise_reward}
            disabled={!teamTrials.enabled}
            onCheckedChange={() =>
              updateTeamTrials({ prioritise_reward: !teamTrials.prioritise_reward })
            }
          />
          Prefer The Rewarded Opponent
          <Tooltips>
            Rarely, one of the three opponents is marked <strong>With Every Win!</strong>
            &nbsp;and pays a reward for each win in the match. With this on the bot picks
            that one; otherwise it always takes the top of the list. If the badge is not
            found it falls back to the top opponent, so the worst case is the behaviour
            you had before.
          </Tooltips>
        </label>
      </div>

      <h3 className="text-xl font-semibold mt-6 mb-2 flex items-center gap-2">
        Daily Tasks
        <Tooltips>
          Free collection the bot does between careers, once a day. Both are picked up by
          the queue the moment the game rolls over to a new day, so nothing here depends
          on your clock or your timezone.
        </Tooltips>
      </h3>

      <div className="grid lg:grid-cols-3 grid-cols-1 gap-2">
        <label className="uma-label col-span-3">
          <Checkbox
            checked={independent.collect_missions}
            onCheckedChange={() =>
              update({ collect_missions: !independent.collect_missions })
            }
          />
          Collect Mission Rewards
          <Tooltips>
            Opens Missions and presses Collect All on every tab &mdash; Daily, Main,
            Titles and Special. Every tab rather than the ones with a badge, because a
            greyed Collect All does nothing at all, and four presses are more certain
            than reading four small red count bubbles.
          </Tooltips>
        </label>

        <label className="uma-label col-span-3">
          <Checkbox
            checked={independent.collect_presents}
            onCheckedChange={() =>
              update({ collect_presents: !independent.collect_presents })
            }
          />
          Collect The Present Box
          <Tooltips>
            One press takes up to a hundred gifts. Worth leaving on: presents expire, and
            the game discards them when they do.
          </Tooltips>
        </label>
      </div>

      <h3 className="text-xl font-semibold mt-6 mb-2 flex items-center gap-2">
        Daily Races
        <Tooltips>
          Six tickets a day, replenished after the server rolls over. Multi-Race spends
          them in one entry rather than six trips, so this is one short visit a day.
        </Tooltips>
      </h3>

      <div className="grid lg:grid-cols-3 grid-cols-1 gap-2">
        <label className="uma-label col-span-3">
          <Checkbox
            checked={independent.daily_races_enabled}
            onCheckedChange={() =>
              update({ daily_races_enabled: !independent.daily_races_enabled })
            }
          />
          Run The Daily Races
          <Tooltips>
            Daily Legend Races are left alone either way.
          </Tooltips>
        </label>

        <label className="uma-label">
          <span>Program</span>
          <Tooltips>
            Which of the two the bot enters. They differ in what they pay and in how far
            they run: Moonlight Sho is Monies over Kyoto Turf 1600m (Mile), Jupiter Cup
            is Support Points over Nakayama Turf 2000m (Medium).
          </Tooltips>
          <select
            className="border-1 border-border rounded-md px-2 py-1 bg-transparent dark:bg-input/30"
            value={independent.daily_race_program}
            disabled={!independent.daily_races_enabled}
            onChange={(e) =>
              update({
                daily_race_program: e.target.value as typeof independent.daily_race_program,
              })
            }
          >
            <option value="moonlight_sho">Moonlight Sho &mdash; Monies, Mile</option>
            <option value="jupiter_cup">Jupiter Cup &mdash; Support Points, Medium</option>
          </select>
        </label>

        <label className="uma-label">
          <span>Difficulty</span>
          <Tooltips>
            All four pay the same kind of reward; the harder ones pay more of it. The bot
            does not check whether your runner can win the one you pick.
          </Tooltips>
          <select
            className="border-1 border-border rounded-md px-2 py-1 bg-transparent dark:bg-input/30"
            value={independent.daily_race_difficulty}
            disabled={!independent.daily_races_enabled}
            onChange={(e) =>
              update({
                daily_race_difficulty:
                  e.target.value as typeof independent.daily_race_difficulty,
              })
            }
          >
            <option value="very_hard">Very Hard</option>
            <option value="hard">Hard</option>
            <option value="normal">Normal</option>
            <option value="easy">Easy</option>
          </select>
        </label>

        <label className="uma-label">
          <span>Tickets Per Day</span>
          <Tooltips>
            How many of the six to spend. They do not carry over &mdash; the stock is
            refilled to the maximum at reset, so anything unspent is gone.
          </Tooltips>
          <Input
            type="number"
            min={1}
            max={6}
            value={independent.daily_race_tickets_per_day}
            disabled={!independent.daily_races_enabled}
            onChange={(e) =>
              update({ daily_race_tickets_per_day: Number(e.target.value) })
            }
          />
        </label>

        {independent.daily_races_enabled && (
          <div className="col-span-3 flex items-start gap-2.5 rounded-md border
                          border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm">
            <AlertTriangle
              size={16}
              className="mt-0.5 shrink-0 text-amber-600 dark:text-amber-400"
            />
            <span>
              <strong>The bot races whoever is already selected.</strong> Runner Selection
              opens pre-filled with the trainee and strategy you last raced with, and the
              bot presses Confirm on it &mdash; it does not pick someone suited to the
              track. The two programs run different distances, so switching program
              without setting your runner up for it will enter a trainee with the wrong
              aptitude.
            </span>
          </div>
        )}
      </div>

      <h3 className="text-xl font-semibold mt-6 mb-2 flex items-center gap-2">
        Waiting And Recovery
        <Tooltips>
          What the bot does instead of stopping. Both of these turn an overnight run that
          ends at 2am into one that is still going in the morning.
        </Tooltips>
      </h3>

      <div className="grid lg:grid-cols-3 grid-cols-1 gap-2">
        <label className="uma-label col-span-3">
          <Checkbox
            checked={independent.wait_for_tp}
            onCheckedChange={() => update({ wait_for_tp: !independent.wait_for_tp })}
          />
          Wait For TP Instead Of Stopping
          <Tooltips>
            Running out of TP holds the career back by roughly how long the missing TP
            takes to come back, at ten minutes a point, and the bot runs Team Trials or
            waits instead. Off restores the old behaviour, where being out of TP ends the
            session.
          </Tooltips>
        </label>

        <label className="uma-label col-span-3">
          <Checkbox
            checked={independent.restart_on_stuck}
            onCheckedChange={() =>
              update({ restart_on_stuck: !independent.restart_on_stuck })
            }
          />
          Restart The Game When Stuck
          <Tooltips>
            For the kinds of stuck a restart can clear &mdash; an unrecognised screen, a
            dialog that will not close, a login that never finishes. Never for being out
            of TP, and never when the account was signed in somewhere else, because
            logging back in there is the swapping war that stop exists to refuse. If a
            restart keeps landing in the same place, it stops for real instead.
          </Tooltips>
        </label>

        <label className={`uma-label ${independent.restart_on_stuck ? "" : "disabled"}`}>
          <span>Restarts Per Session</span>
          <Tooltips>
            After this many the bot stops rather than restarting again. 0 disables
            restarting entirely, the same as the switch above.
          </Tooltips>
          <Input
            className="w-20"
            type="number"
            min={0}
            disabled={!independent.restart_on_stuck}
            value={independent.restart_max_per_session}
            onChange={(e) =>
              update({ restart_max_per_session: Math.max(0, e.target.valueAsNumber || 0) })
            }
          />
        </label>

        <label className={`uma-label ${independent.restart_on_stuck ? "" : "disabled"}`}>
          <span>Restarts For The Same Problem</span>
          <Tooltips>
            How many times in a row the game is restarted for the same kind of stuck before
            the bot gives up and stops. The emulator sometimes needs a few tries to come
            back from a freeze; a problem no restart can fix is still stopped after this
            many. A different problem in between starts the count again, and Restarts Per
            Session still caps the total.
          </Tooltips>
          <Input
            className="w-20"
            type="number"
            min={1}
            disabled={!independent.restart_on_stuck}
            value={independent.restart_max_same_kind}
            onChange={(e) =>
              update({ restart_max_same_kind: Math.max(1, e.target.valueAsNumber || 1) })
            }
          />
        </label>

        <label className="uma-label">
          <span>Signed In Elsewhere, Wait (minutes)</span>
          <Tooltips>
            When the game says the account was signed in on another device, everything
            stops for this long rather than logging straight back in &mdash; which would
            sign that device out and start the two taking turns kicking each other off.
            Set it to 0 to stop instead and be told about it.
          </Tooltips>
          <Input
            className="w-20"
            type="number"
            min={0}
            value={independent.session_conflict_wait_minutes}
            onChange={(e) =>
              update({
                session_conflict_wait_minutes: Math.max(0, e.target.valueAsNumber || 0),
              })
            }
          />
        </label>

        <label className={`uma-label col-span-2 ${independent.restart_on_stuck ? "" : "disabled"}`}>
          <span>Game Package</span>
          <Tooltips>
            Leave empty and the bot reads it off the device, which is almost always what
            you want &mdash; this install is com.cygames.umamusume and the JP build
            differs. Run <code>py devtools/adb_probe.py package</code> to see it.
          </Tooltips>
          <Input
            className="w-64"
            type="text"
            placeholder="read from the device"
            disabled={!independent.restart_on_stuck}
            value={independent.game_package}
            onChange={(e) => update({ game_package: e.target.value.trim() })}
          />
        </label>
      </div>

    </div>
  );
}
