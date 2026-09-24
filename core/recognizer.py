"""Telling a lit control from a dimmed one by how bright it is.

The game greys out a button it will not accept rather than hiding it, so the shape
matches either way and template matching alone cannot tell the two apart. What does
tell them apart is overall brightness: a crop of the dimmed version sits measurably
darker than the reference image of the lit one.

One function, used by the skill screen to decide whether a skill is affordable. The
rest of this module -- a commented-out pair of matchers, a box de-duplicator that
`utils.device_action_wrapper` had its own working copy of, and four colour helpers with
no callers at all -- was removed on 2026-09-20.
"""
import cv2
import numpy as np

from utils.log import debug, args


def compare_brightness(template_path: str, other: np.ndarray, brightness_diff_threshold=0.025):
  """Whether `other` is about as bright as the image at `template_path`.

  The comparison is relative, not absolute: the difference is divided by the reference's
  own brightness, so the threshold means the same thing for a dark icon as for a pale
  one. Both sides are reduced to greyscale first, because the question is how lit the
  control is, not what colour it is.

  Both sides must be reduced the *same* way, and until 2026-09-20 this did it two
  different ways at once, each wrong on its own:

  1. The capture was reduced with `COLOR_BGR2GRAY`, but a capture here is RGB (both
     device layers convert on the way out -- see `device_action_wrapper.screenshot`).
     Greyscale is a weighted sum and red and blue carry very different weights, so that
     swapped them: 161.84 read as 142.75 for the skill screen's "+" icon.
  2. The reference was loaded with `IMREAD_GRAYSCALE`, which lets the PNG codec do its
     own conversion rather than the one `cvtColor` applies. Same file, same pixels, no
     transparency: 174.11 that way against 161.84 through cvtColor, a 7.6% gap that has
     nothing to do with channel order.

  Together they put the template 0.18 away from *itself* on a threshold of 0.20 -- a
  fully-lit "+" sat within 0.02 of being called greyed-out, and any render change would
  have tipped it into silently skipping skill purchases.

  So the reference is now loaded exactly as a capture arrives and reduced by the same
  call. An image compared against itself scores 0, which is the property
  `devtools/check_brightness.py` holds this to.
  """
  reference = cv2.cvtColor(cv2.imread(template_path, cv2.IMREAD_COLOR), cv2.COLOR_BGR2GRAY)
  reference_brightness = np.mean(reference)
  measured = np.mean(cv2.cvtColor(other, cv2.COLOR_RGB2GRAY))
  difference = abs(measured - reference_brightness) / reference_brightness
  # Per-call detail behind --device-debug: the skill survey calls this once per row per
  # page, which came to over a thousand lines in a single career.
  if args.device_debug:
    debug(f"Brightness diff: {difference:.3f}, threshold: {brightness_diff_threshold}")
  return difference <= brightness_diff_threshold
