[Back to main readme](./README.md)

# Credits

Mirako Machine began as a fork of
[samsulpanjul/umamusume-auto-train](https://github.com/samsulpanjul/umamusume-auto-train),
by way of [CrazyIvanTR/umamusume-auto-train](https://github.com/CrazyIvanTR/umamusume-auto-train),
and the original project was itself inspired by
[shiokaze/UmamusumeAutoTrainer](https://github.com/shiokaze/UmamusumeAutoTrainer).

The screen-driven state machine at the heart of this bot — identify what is on screen,
then hand it to a handler — is theirs, as is the template-matching approach it rests on,
the OCR plumbing, the screenshot and input layers, and the web UI's first shape. Those
still run here, largely as they were written.

The licensing of the upstream projects is unconfirmed — no licence covering the upstream
code that remained in this tree has ever been established — so this project is governed
by [LICENSE](./LICENSE) and [LICENSE_AND_DISCLAIMER.md](./LICENSE_AND_DISCLAIMER.md), which
give no rights in or to the upstream projects.

## The people

- **[Samsul Panjul](https://ko-fi.com/samsulpanjul)** — created the original project.
- **[CrazyIvanTR](https://buymeacoffee.com/crazyivantr)** (Seçkin Ozan Uyan) — maintained
  and developed it through the fork this one was taken from. More of the history is his
  than anyone else's, and so is most of what remains here.
- And everyone else whose commits are in `git log` — several of whom still have code in
  this tree.

If you find this bot useful, the two links above are where to say so: neither author is
involved in this fork, and neither is responsible for anything it does.

## What changed here

This build runs **Independent Training** only — the mode where the game plays a whole
career by itself — and the turn-by-turn career automation the upstream projects are built
around has been removed rather than extended. On top of that: a task queue and scheduler,
several emulators driven from one web UI with a config each, scenario and support-deck
selection, a rewritten skill-buying pass that optimises for rating, a borrow-card library
covering every released SSR and SR, an offline test suite of 35 checks with mutation
testing as the standard, and versioned releases with an update check.

Roughly four fifths of the current source is new, and most of the rest has been reworked.
`git log` and `git blame` are the honest record of who wrote what.

## Third-party

- UI components generated from [shadcn/ui](https://ui.shadcn.com) (MIT).
- Game data scraped from [GameTora](https://gametora.com) and [uma.guide](https://uma.guide).
- Python and JavaScript dependencies are listed in `requirements.txt` and
  `web/package.json`, each under its own licence.
