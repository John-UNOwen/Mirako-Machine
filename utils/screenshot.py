"""Preparing a piece of the screen for OCR.

The game draws small, tightly-kerned text over busy artwork, and the OCR reads it far
better after the same three steps every time: double the size, drop the colour, lift the
contrast. Both functions here end in that treatment, and there are only two because a
caller either has a region it wants grabbed and prepared, or already holds a crop it has
worked on itself and wants prepared identically.

That last part is the whole reason this is one place and not several. Two readings of a
region are only comparable if both were prepared the same way, so `enhance_for_ocr_text`
is the single decision about how -- see the second read in the stat-sanity check, which
crops and repaints a cell before asking again.

Seven other helpers lived here: a binariser, a noise cleaner, a grabcut segmenter, a
centroid finder, a screenshot comparator, a second OCR preparation with a different
recipe, and an 86-line reader for the "+N" gain marker. None had a caller anywhere in
the tree; they were removed on 2026-09-20.
"""
from PIL import Image, ImageEnhance

import utils.device_action_wrapper as device_actions
from utils.log import debug_window, args

# Enough to put the game's small text comfortably above the OCR's minimum height without
# inventing detail that is not in the pixels.
SCALE = 2

# Mild on purpose. Enough to separate text from the artwork behind it; more than this and
# anti-aliased strokes break up, which costs more reads than the contrast gains.
CONTRAST = 1.5


def enhance_for_ocr_text(image_rgb, debug_flag=False) -> Image.Image:
  """A crop, upscaled, greyscaled and contrast-lifted, ready to read.

  Takes anything PIL can build an image from -- an array from a screenshot, or a crop a
  caller has already altered. Each step can be dumped to the debug window, because when
  a read comes back wrong the question is always which step lost the text.
  """
  if args.device_debug:
    debug_flag = True

  prepared = Image.fromarray(image_rgb)
  prepared = prepared.resize((prepared.width * SCALE, prepared.height * SCALE), Image.BICUBIC)
  if debug_flag:
    debug_window(prepared, save_name="enhanced_screenshot_resized")

  prepared = prepared.convert("L")
  if debug_flag:
    debug_window(prepared, save_name="enhanced_screenshot_contrast")

  prepared = ImageEnhance.Contrast(prepared).enhance(CONTRAST)
  if debug_flag:
    debug_window(prepared, save_name="enhanced_screenshot_contrast_enhanced")

  return prepared


def enhanced_screenshot(region=(0, 0, 1920, 1080), debug_flag=False) -> Image.Image:
  """Grab `region` (x, y, w, h) from the game and prepare it for OCR."""
  if args.device_debug:
    debug_flag = True

  grabbed = device_actions.screenshot(region_xywh=region)
  if debug_flag:
    debug_window(grabbed, save_name="enhanced_screenshot")

  return enhance_for_ocr_text(grabbed, debug_flag=debug_flag)
