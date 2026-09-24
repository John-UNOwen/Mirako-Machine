"""Choosing which saved race agenda a career loads, on the My Agendas screen.

My Agendas is a scrolling list of eight saved agendas. Each card has a name the user can
change -- an unnamed one reads "Agenda N" -- two race thumbnails, and Save Here and Load
List buttons. The bot used to press the topmost Load List, which is slot 1 because the
list opens at the top every time. This picks any slot, by position or by name.

Three things about that screen decide the design, and each was measured on a live
emulator rather than assumed:

  * **A drag does not move the list by its own length.** A slow 186px drag moved it
    214px; the wheel emulation in device_action.scroll glides ~18px past its notch too.
    Counting drags would drift a card every few steps, and loading the wrong agenda
    costs a whole career, so every scroll is *measured* -- the list before and after are
    registered against each other -- and a card's slot is arithmetic on that, not a guess.
  * **Names are not unique.** The account this was built on has two agendas called
    CROWN, and two more that differ only in their name. A name therefore selects the
    first card that carries it, top to bottom, and matching is exact once normalised --
    the same rule as the support deck, and for its reason: "Agenda 1" is most of
    "Agenda 10".
  * **Save Here sits beside every Load List.** A tap there overwrites a saved agenda.
    So nothing here taps a fixed point: the drag is anchored on the inert G1/G2/G3
    column, and the only press is on a Load List that has just been matched inside the
    card that was chosen.

Everything below works on the list's own crop, so its geometry does not depend on which
frame the constants are in; the crop's box and the drag anchor are the only coordinates,
and they live in utils/constants.py with the rest. Crops are RGB, as every capture in
this repo is -- cv2.imread's BGR is converted at the edge, never assumed.
"""
import cv2
import numpy as np

from core.independent_borrow import normalise

LOAD_TEMPLATE = "assets/independent/load_list_btn.png"
LOAD_CONFIDENCE = 0.8

# The header bar's green, and how much of a row has to be it. Sampled on the middle of the
# card, clear of the Load List button -- also green, but a fifth of that span at most.
HEADER_BAND = (175, 475)
HEADER_MIN_FILL = 0.6
# A full header is ~20 rows. Fewer means the bar is cut by the list's edge, and a cut
# header's centre is not where its card is.
HEADER_MIN_ROWS = 15

# Where a header's name sits, relative to the crop and to the header's centre.
NAME_SPAN = (12, 440)
NAME_HALF_HEIGHT = 11

# Registration searches shifts up to this far, which has to stay under a card's pitch so
# that a true shift inside it has only one answer. That is not enough on its own: a shift
# *beyond* the window aliases into it, one pitch short, and scores well when neighbouring
# cards look alike -- measured, 214px read as 28 at a mismatch of 7.4. The scrollbar check
# in find_agenda is what catches that. A notch plus its glide is ~120px.
MAX_SHIFT = 180
# Mean absolute difference, 0-255, below which a registration is believed. Measured on
# live captures: a true alignment scores 2-6 -- not 0, because the race thumbnails
# sparkle between frames -- two pixels off already scores 16, and the best wrong
# alignment anywhere within one card's pitch scored 24.
MAX_SHIFT_ERROR = 12.0
# How far a header may sit from where its slot says it should be. The lattice is exact to
# a pixel or two; more than this means a measurement went wrong, and the scan stops
# rather than pressing a button it has miscounted its way to.
LATTICE_TOLERANCE = 12

MAX_NOTCHES = 24

# The scrollbar. Brightness bands for its column, and the list viewport's height that
# relates thumb pixels to list pixels -- measured, not a guess: 2.892, 2.912 and 2.918
# list px per thumb px at offsets 214, 562 and 966, against 543 / 186.5 = 2.912.
THUMB_BELOW = 170
THUMB_OR_TRACK_BELOW = 232
VIEWPORT = 543
# Within this many list pixels of the top counts as at the top: the thumb's own edge is
# read to a pixel, three list pixels.
AT_TOP = 6
# How far the two measurements may disagree before the scan stops. A miscount is a whole
# card, 186px; the scrollbar's own error measured +3, +3, +4.5 and +13.8 at offsets 0,
# 214, 562 and 966 -- worst at the far end, where the thumb reads a pixel or two short.
BAR_AGREEMENT = 60


class AgendaError(Exception):
  """Why a configured agenda could not be loaded. The caller stops with the message."""


def header_rows(crop):
  """Centres of the full card headers in a list crop, top to bottom, crop coordinates."""
  if crop is None or crop.size == 0:
    return []
  band = crop[:, HEADER_BAND[0]:HEADER_BAND[1]].astype(int)
  red, green, blue = band[..., 0], band[..., 1], band[..., 2]
  is_green = (green > 150) & (red < 170) & (blue < 90) & (green - red > 40)
  rows = is_green.mean(axis=1) >= HEADER_MIN_FILL
  centres, start = [], None
  for y, on in enumerate(list(rows) + [False]):
    if on and start is None:
      start = y
    elif not on and start is not None:
      # Touching either edge means cut by it, however many rows show.
      if y - start >= HEADER_MIN_ROWS and start > 0 and y < len(rows):
        centres.append((start + y - 1) // 2)
      start = None
  return centres


def measure_shift(before, after, max_shift=MAX_SHIFT):
  """How far the list moved up between two crops, in pixels: (shift, error).

  The content that was at row r+s of `before` is at row r of `after`. Found by trying
  every shift and keeping the one where the overlap agrees best; `error` is that
  agreement, so the caller can refuse a registration that never really lined up.
  """
  a = cv2.cvtColor(before, cv2.COLOR_RGB2GRAY).astype(np.float32)
  b = cv2.cvtColor(after, cv2.COLOR_RGB2GRAY).astype(np.float32)
  height = min(len(a), len(b))
  best_shift, best_error = 0, float("inf")
  for shift in range(0, min(max_shift, height - 150) + 1):
    error = float(np.mean(np.abs(b[:height - shift] - a[shift:height])))
    if error < best_error:
      best_shift, best_error = shift, error
  return best_shift, best_error


def slot_at(header_y, offset, first_header_y, pitch):
  """The 1-based slot of the card whose header is at `header_y`, or None if off-lattice."""
  position = header_y + offset - first_header_y
  index = round(position / pitch)
  if abs(position - index * pitch) > LATTICE_TOLERANCE or index < 0:
    return None
  return index + 1


def load_button_in(crop, header_y, pitch, template=None):
  """The Load List button inside the card whose header is at `header_y`, or None.

  Searched only within that card's own band, so the press cannot land on a neighbour's
  button however the list has settled. Returns its centre in crop coordinates, and only
  when the whole button is inside the crop -- a half-visible button is not one to press.
  """
  if template is None:
    loaded = cv2.imread(LOAD_TEMPLATE)
    if loaded is None:
      raise FileNotFoundError(LOAD_TEMPLATE)
    template = cv2.cvtColor(loaded, cv2.COLOR_BGR2RGB)
  top, bottom = max(0, header_y), min(len(crop), header_y + pitch)
  band = crop[top:bottom]
  th, tw = template.shape[:2]
  if band.shape[0] < th or band.shape[1] < tw:
    return None
  scores = cv2.matchTemplate(band, template, cv2.TM_CCOEFF_NORMED)
  _, best, _, (x, y) = cv2.minMaxLoc(scores)
  if best < LOAD_CONFIDENCE:
    return None
  return (x + tw // 2, top + y + th // 2)


def read_name(crop, header_y, reader):
  """The name on the header at `header_y`, via `reader(image) -> str`."""
  top = max(0, header_y - NAME_HALF_HEIGHT)
  bottom = min(len(crop), header_y + NAME_HALF_HEIGHT + 1)
  return (reader(crop[top:bottom, NAME_SPAN[0]:NAME_SPAN[1]]) or "").strip()


def names_match(wanted, observed):
  """Exact once case, spaces and punctuation are gone. Never fuzzy -- see the module."""
  return bool(normalise(wanted)) and normalise(wanted) == normalise(observed)


class ListNotAtTop(AgendaError):
  """The list was not at its top when the scan began. Closing and reopening it fixes that:
  the game opens My Agendas at the top every time."""


def scrollbar(bar):
  """(track_top, thumb_top, thumb_length) from the scrollbar's column, or None.

  Rows are sorted by brightness: the page behind the bar is ~241, the track ~211 and the
  thumb ~125. None when there is no thumb at all -- a list short enough not to scroll.
  """
  if bar is None or bar.size == 0:
    return None
  lum = bar.astype(float).mean(axis=(1, 2))
  on_bar = np.where(lum < THUMB_OR_TRACK_BELOW)[0]
  thumb = np.where(lum < THUMB_BELOW)[0]
  if not len(on_bar) or not len(thumb):
    return None
  return int(on_bar[0]), int(thumb[0]), int(thumb[-1] - thumb[0] + 1)


def bar_offset(bar):
  """How far the list is scrolled, in list pixels, read off the scrollbar alone.

  A scrollbar's thumb is to its track what the viewport is to the content, so one thumb
  pixel is VIEWPORT / thumb_length list pixels -- 2.91 on an account of eight agendas,
  measured at four known offsets to within 0.03. Accurate to a few pixels: enough to say
  which card is which, which is all it is asked.
  """
  found = scrollbar(bar)
  if found is None:
    return 0.0
  track_top, thumb_top, thumb_length = found
  return (thumb_top - track_top) * VIEWPORT / max(1, thumb_length)


def find_agenda(wanted, capture, scroll_down, read_text, pitch, log=print):
  """Walk the list from the top to the wanted agenda. Returns (slot, name, point).

  `wanted` is ("slot", n) or ("name", text). `capture()` returns (list, scrollbar) crops,
  RGB, from one frame, once the list has stopped moving; `scroll_down()` moves it one
  notch; `read_text` OCRs a header. `point` is the Load List centre in crop coordinates
  -- the caller adds the crop's origin and presses it. Raises AgendaError when it cannot
  be reached, and ListNotAtTop when the list was not where a scan has to begin.

  Two measurements, which have to agree. Registering each frame against the last gives
  the scroll exactly, but periodically: cards are one pitch apart and on a real account
  several look alike, so a scroll of 214px registered as 28 with a comfortable score.
  The scrollbar gives the scroll absolutely, to a few pixels. Every step is checked
  against it, and a disagreement ends the scan before anything is pressed.
  """
  kind, value = wanted
  crop, bar = capture()
  if bar_offset(bar) > AT_TOP:
    raise ListNotAtTop("the agenda list was not at its top")
  headers = header_rows(crop)
  if not headers:
    raise AgendaError("no agenda cards could be found on My Agendas")
  first_header = headers[0]
  offset, seen = 0, {}

  for _ in range(MAX_NOTCHES + 1):
    for header in header_rows(crop):
      slot = slot_at(header, offset, first_header, pitch)
      if slot is None:
        raise AgendaError(f"a card header sat off the list's spacing at {header}px, so "
                          "the scroll was mismeasured; not pressing anything")
      if slot not in seen:
        seen[slot] = read_name(crop, header, read_text) if kind == "name" else ""
        if kind == "name":
          log(f"Agenda {slot}: {seen[slot]!r}")
      chosen = (value == slot) if kind == "slot" else names_match(value, seen[slot])
      if not chosen:
        continue
      point = load_button_in(crop, header, pitch)
      if point is not None:
        # Read by slot, it is still worth naming in the log: "agenda 5, 'fan'" says what
        # the career got in a way a number alone does not.
        return slot, seen[slot] or read_name(crop, header, read_text), point
      # Chosen, but its button is below the fold: one more notch brings it into view.

    scroll_down()
    after, after_bar = capture()
    shift, error = measure_shift(crop, after)
    if error > MAX_SHIFT_ERROR:
      raise AgendaError(f"could not tell how far the agenda list moved (mismatch "
                        f"{error:.1f}); not pressing anything")
    if shift == 0:
      break
    offset += shift
    absolute = bar_offset(after_bar)
    if abs(absolute - offset) > BAR_AGREEMENT:
      raise AgendaError(f"the agenda list's scroll was measured as {offset}px by its "
                        f"cards and {absolute:.0f}px by its scrollbar -- a whole card "
                        "apart is a miscount -- so not pressing anything")
    crop = after

  total = max(seen) if seen else 0
  if kind == "slot":
    raise AgendaError(f"there is no agenda {value}; this account has {total}")
  listed = ", ".join(f"{slot}: {name or '(unreadable)'}" for slot, name in sorted(seen.items()))
  raise AgendaError(f"no agenda is named {value!r} ({listed})")
