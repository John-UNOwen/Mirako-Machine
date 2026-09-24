"""Regenerate the Independent Training anchor/button templates.

The templates in assets/independent/ are mechanical crops out of the reference
screenshots in references/independent_training/. Keeping the crop boxes here (rather
than cropping by hand) means the whole asset set can be rebuilt in one command when the
game shifts its UI -- just re-take the reference screenshots and rerun this.

Usage:
  py devtools/crop_independent_assets.py            # write assets/independent/*.png
  py devtools/crop_independent_assets.py --check    # verify existing assets still match

Every reference screenshot must be a native 1920x1080 capture, since all boxes are in
the same "world" coordinate space the bot's constants use.
"""

import argparse
import os
import sys

import cv2

REF_DIR = "references/independent_training"
OUT_DIR = "assets/independent"

EXPECTED_SIZE = (1920, 1080)  # (width, height)

# name -> (source screenshot, (x1, y1, x2, y2))
#
# Anchors are chosen to be *distinctive* (won't collide with another screen) and
# *stable* (no timers, counts, character names or other per-run values inside the box).
# Where two screens share a generic title like "Confirmation", the anchor is taken from
# the body text instead, which is what actually differs.
CROPS = {
    # --- setup phase -------------------------------------------------------
    # Flanking art is fixed home-screen dressing (not the player's trainee), so it is
    # kept -- more distinctive pixels match more reliably. y2 stops short of the orange
    # "Event" banner underneath, which is only present while an event is running.
    "home_career_btn":          ("1.png",        (645, 908, 792, 947)),
    "scenario_select_label":    ("2.png",        (160, 138, 285, 160)),
    "trainee_select_label":     ("3.png",        (160, 138, 285, 160)),
    "legacy_select_label":      ("4.png",        (160, 138, 285, 160)),
    "support_formation_label":  ("5.png",        (160, 138, 310, 160)),
    # Whole pink slot incl. its "Friends" caption -- a bare "+" on white is too
    # generic and collides with other screens.
    "friends_slot_empty":       ("5.png",        (655, 472, 812, 686)),
    "borrow_card_title":        ("6.png",        (440,  38, 668,  72)),
    # Green button + label only. A chibi of the *selected trainee* overlaps the left
    # edge of this button (x<=500) and changes every career, so it is cropped out.
    "start_career_btn":         ("7.png",        (514, 883, 663, 928)),
    "final_confirmation_title": ("8.png",        (432,  38, 676,  72)),
    "tab_independent_inactive": ("8.png",        (558, 168, 840, 197)),
    "tab_independent_active":   ("9.png",        (558, 168, 840, 197)),
    "agenda_edit_btn":          ("9.png",        (737, 503, 823, 534)),
    "agenda_title":             ("10.png",       (458,  38, 650,  72)),
    "my_agendas_btn":           ("10.png",       (690, 897, 843, 938)),
    "my_agendas_title":         ("11.png",       (440,  38, 668,  72)),
    "load_list_btn":            ("11.png",       (696, 479, 816, 511)),
    "schedule_race_body":       ("12.png",       (280, 508, 828, 536)),
    "start_btn":                ("14.png",       (572, 974, 800, 1021)),
    "confirm_independent_body": ("15.png",       (296, 452, 812, 478)),

    # --- run ---------------------------------------------------------------
    # Solid opaque green banner, and cropped to the right of the scenario logo so it
    # stays valid across scenarios. This is the anchor for "a run is in progress".
    #
    # Deliberately NOT used: the "Training..." caption at the bottom of the same screen.
    # Its bar is semi-transparent over the trainee artwork (sampled background differs
    # left-to-right: 91,85,73 vs 72,66,77), so the template would drift per career.
    "training_banner_label":    ("training.png", (240,  74, 450,  99)),

    # --- teardown ----------------------------------------------------------
    "training_log_title":       ("16.png",       (386,  16, 718,  84)),
    # Icons + "Skills" only. The "Skill Pts 3791" strip underneath (y>=903) is per-run
    # data and must stay out of the template.
    "skills_pill_btn":          ("17.png",       (332, 850, 502, 902)),
    # The "!" the game puts on the Skills button while something on the skill screen is
    # still affordable -- a far better "is there anything left to buy" signal than any
    # balance threshold, because the game works it out from the real prices.
    #
    # The badge is a 34px circle at (511,837)-(544,870) sitting directly on scene
    # artwork that changes with the trainee, so the crop is the largest square that
    # fits *inside* the circle (34/sqrt(2) ~ 24). Taking the full circle would bake
    # this trainee's background into the corners of the template.
    "skills_badge":             ("17.png",       (516, 842, 540, 866)),
    # Solid green "Skill Points" pill, cropped to exclude the balance to its right.
    #
    # Deliberately NOT used as anchors: the "Learn" and "Complete Career" captions in
    # the top-left of these two screens. Both sit on a semi-transparent bar over scene
    # artwork, and both start at x=152 -- three columns left of GAME_WINDOW_BBOX, so
    # part of the template falls outside the frame the bot actually captures.
    # Screen 17 is anchored on the existing assets/buttons/complete_career_btn.png.
    "learn_skill_points_label": ("18.png",       (502, 337, 660, 376)),
    # "Learn the above skills?" on the modal that Confirm opens. Anchored on the
    # prompt rather than the "Confirmation" header, which other modals share.
    "learn_confirm_prompt":     ("28.png",       (430, 905, 675, 933)),
    # "Your trainee learned new skills!" on the receipt that follows Learn. The
    # green "Skills Learned" header is not distinctive enough on its own -- several
    # modals share that banner -- so the body text is the anchor.
    "skills_learned_body":      ("30.png",       (418, 628, 688, 660)),
    # The "Complete Career" confirmation that Complete Career opens. Anchored on the
    # prompt, since the modal's green header repeats the screen name behind it.
    "complete_confirm_body":    ("confirmation.png", (408, 548, 705, 580)),
    "finish_btn":               ("confirmation.png", (569, 743, 804, 805)),
    "sparks_title":             ("19.png",       (410,  14, 700,  76)),
    "keep_sparks_body":         ("20.png",       (430, 884, 678, 908)),
    "uma_details_title":        ("21.png",       (422,  38, 686,  72)),
    "rewards_title":            ("23.png",       (385,   8, 725,  62)),
    "career_complete_title":    ("27.png",       (442, 330, 664, 366)),
    "to_home_btn":              ("27.png",       (307, 679, 529, 729)),

    # --- connection loss and recovery ------------------------------------------
    # The two "Connection Error" modals differ only in their wording and in whether a
    # Retry button is offered, so the body text is what tells them apart. The error
    # code line ("Error code: 390") is deliberately outside both boxes -- the number
    # changes.
    #
    # A note on coordinates here: the title screen is drawn full-screen landscape,
    # unlike the portrait play area the rest of these boxes live in, so its logo runs
    # from x690 to x1295 and the fatal modal is centred on 1920 rather than on the
    # play area. Boxes must stay inside GAME_WINDOW_BBOX (x155-955) to be matchable
    # against the frame the bot captures, so both take the part that falls inside it.
    "connection_error_retry":   ("error.png",    (410, 505, 700, 540)),
    "connection_error_fatal":   ("error2.png",   (816, 488, 950, 522)),
    # Left half of a "Title Screen" button whose full width reaches x1080. Its centre
    # still lands on the button, so clicking the match works. This also matches the
    # same button on the retryable modal (0.935), which is harmless: it is only ever
    # clicked once the fatal modal has been identified by its body text.
    "title_screen_btn":         ("error2.png",   (845, 670, 950, 728)),
    # Logo letters only. "TAP TO START" underneath flashes, and the anniversary
    # subtitle below that changes with the event, so neither can anchor this screen.
    # The logo is also what gets clicked to leave the title screen.
    "title_logo":               ("menu.png",     (690, 645, 950, 735)),
    # "Training Independently" over the CAREER button, shown while a career is still
    # running. The button art itself cannot anchor this: the trainee's chibi stands in
    # front of it and changes with the trainee.
    # The daily server reset. At midnight JST the game rolls the date over, shows this
    # over whatever was on screen, and drops the session to the title screen. Anchored on
    # the body rather than the "Date Changed" header, matching how the other modals here
    # are pinned. Its OK is the shared assets/buttons/ok_btn.png, which scores 0.982.
    "date_changed_body":        ("new day.png",  (474, 502, 640, 538)),
    "training_independently":   ("ongoing training.png", (600, 814, 812, 848)),
    # The same slot once the career has finished but its results have not been collected
    # -- reached when an interruption outlasts the run. Cropped to the pill's text only:
    # the trainee's chibi is drawn over the pill's lower left and is a different chibi
    # every career, which is also why the CAREER button beneath is clicked by position
    # rather than matched (career_in_progress_btn scores 0.796 here).
    "home_post_career":         ("contine finish.png", (640, 818, 790, 843)),
    # ...which is also why the click target is the right-hand end of the CAREER
    # button, clear of wherever the chibi happens to stand.
    "career_in_progress_btn":   ("ongoing training.png", (712, 898, 830, 954)),
    # Pressing CAREER on that home screen does not go straight back into the career:
    # it opens an "Independent Training" panel showing the scenario, the trainee and
    # the time left, and its own Career button is what actually resumes. The header is
    # the anchor -- the panel's body carries the countdown and the trainee's name,
    # both of which change.
    "continue_training_header": ("continue.png",  (435, 336, 675, 374)),
    "continue_career_btn":      ("continue.png",  (570, 675, 790, 732)),

    # --- TP refill -------------------------------------------------------------
    # The green "+" beside the TP bar on the home screen, which opens Recover TP.
    "tp_plus_btn":              ("1.png",        (551,  32, 588,  69)),
    # Item names in the Recover TP list. These are matched rather than trusting row
    # positions: an item that runs out disappears from the list entirely, so row 2
    # stops being Toughness 30 and becomes a Handmade Chocolate. Clicking by position
    # would then spend an item that is deliberately not on the permitted list.
    "recover_tp_carats":        ("refill1.png",  (375, 115, 435, 145)),
    "recover_tp_toughness":     ("refill1.png",  (375, 232, 500, 263)),
    # Every row's Use button is identical, so the click is aimed by searching for this
    # within the matched item's row rather than by taking the best match on screen.
    "recover_tp_use_btn":       ("refill1.png",  (718, 131, 812, 175)),
    # The two use dialogs, told apart by their blurb. The item dialog arrives with a
    # quantity of 1 already set; the carats dialog arrives at 0 with OK disabled, so
    # it needs the "+" pressed once per 30 TP.
    "tp_use_item_body":         ("refill2.png",  (368, 466, 745, 498)),
    "tp_use_carats_body":       ("refill3.png",  (408, 466, 705, 498)),
    "tp_carats_plus_btn":       ("refill3.png",  (668, 548, 718, 598)),
    # Both receipts are the same screen and both just need Close; the wording differs,
    # so the spec carries one anchor for each. Quantity is always one unit, which is
    # what keeps "1 Toughness 30" and "10 Carats" stable enough to match on.
    "tp_used_item_body":        ("refill4.png",  (376, 524, 740, 558)),
    "tp_used_carats_body":      ("refill6.png",  (406, 524, 715, 558)),

    # --- racing style ----------------------------------------------------------
    # The Lineup Details chevron on the Final Confirmation screen, which is also what
    # says whether the lineup is expanded: it points down when collapsed and right when
    # open. Same box for both, and they separate cleanly -- each scores 1.000 on its own
    # state and 0.878 on the other.
    "lineup_expand_btn":        ("style1.png",   (777, 291, 821, 335)),
    "lineup_collapse_btn":      ("style2.png",   (777, 291, 821, 335)),
    # Opens the Strategy dialog. Not used to identify the expanded state: the Normal
    # Career tab has a Change button of its own that scores 0.976 against this.
    "strategy_change_btn":      ("style2.png",   (732, 457, 820, 498)),
    "strategy_title":           ("style3.png",   (500, 258, 600, 292)),
    # The four style buttons, cropped to the word alone. Each carries the trainee's
    # aptitude grade beside it -- End B, Late A, Pace A, Front G on this capture -- and
    # those change with the trainee, so they are left outside the box. The selected
    # button also gains green corner brackets, which sit on the border rather than the
    # label, so one template covers both states.
    "style_end_btn":            ("style3.png",   (325, 630, 368, 676)),
    "style_late_btn":           ("style3.png",   (462, 630, 501, 676)),
    "style_pace_btn":           ("style3.png",   (587, 630, 632, 676)),
    "style_front_btn":          ("style3.png",   (715, 630, 761, 676)),
}


def _load_reference(filename):
    path = os.path.join(REF_DIR, filename)
    image = cv2.imread(path, cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(f"Reference screenshot not found: {path}")
    height, width = image.shape[:2]
    if (width, height) != EXPECTED_SIZE:
        raise ValueError(
            f"{path} is {width}x{height}, expected {EXPECTED_SIZE[0]}x{EXPECTED_SIZE[1]}. "
            "Templates must be cropped from native 1080p captures."
        )
    return image


def crop_all(check_only=False):
    references = {}
    failures = []
    written = 0

    for name, (source, box) in CROPS.items():
        if source not in references:
            references[source] = _load_reference(source)
        x1, y1, x2, y2 = box
        patch = references[source][y1:y2, x1:x2]
        if patch.size == 0:
            failures.append(f"{name}: crop box {box} is empty")
            continue

        out_path = os.path.join(OUT_DIR, f"{name}.png")
        if check_only:
            existing = cv2.imread(out_path, cv2.IMREAD_COLOR)
            if existing is None:
                failures.append(f"{name}: missing {out_path}")
            elif existing.shape != patch.shape:
                failures.append(f"{name}: size drift {existing.shape} vs {patch.shape}")
            elif cv2.norm(existing, patch, cv2.NORM_L1) != 0:
                failures.append(f"{name}: content differs from crop box")
        else:
            cv2.imwrite(out_path, patch)
            written += 1
            print(f"  {patch.shape[1]:4d}x{patch.shape[0]:<4d} {out_path}")

    return written, failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="verify existing assets match the crop boxes instead of rewriting")
    parsed = parser.parse_args()

    if not parsed.check:
        os.makedirs(OUT_DIR, exist_ok=True)

    written, failures = crop_all(check_only=parsed.check)

    if failures:
        print(f"\n{len(failures)} problem(s):")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    if parsed.check:
        print(f"All {len(CROPS)} templates match their crop boxes.")
    else:
        print(f"\nWrote {written} templates to {OUT_DIR}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
