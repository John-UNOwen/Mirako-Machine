"""One-off experiment: which template-matching normalization is rendering-agnostic?

Scores fragile templates on both the desktop references they were cropped from and the
live ADB frames, under several preprocessing strategies. The goal is a matcher whose
true-match score is high on BOTH rendering pipelines, so a single template library keeps
working and per-platform thresholds stop being needed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np

import scenarios.independent_screens as s


def prep(img, method):
  g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
  if method == "rgb":
    return img
  if method == "gray":
    return g
  if method == "gray_blur":
    return cv2.GaussianBlur(g, (5, 5), 0)
  if method == "clahe":
    return cv2.createCLAHE(2.0, (8, 8)).apply(g)
  if method == "down":
    h, w = g.shape
    return cv2.resize(g, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
  if method == "edges":
    return cv2.Canny(g, 50, 150)
  raise ValueError(method)


def match(hay, tmpl_rgb, method):
  h = prep(hay, method)
  t = prep(tmpl_rgb, method)
  result = cv2.matchTemplate(h, t, cv2.TM_CCOEFF_NORMED)
  return float(result.max())


PAIRS = [
  ("assets/independent/training_independently.png", "logs/fresh_adb_frame.png", "ongoing training.png"),
  ("assets/independent/career_in_progress_btn.png", "logs/fresh_adb_frame.png", "ongoing training.png"),
  ("assets/independent/home_career_btn.png", "logs/fresh_adb_frame.png", "1.png"),
  ("assets/independent/tp_plus_btn.png", "logs/fresh_adb_frame.png", "ongoing training.png"),
  ("assets/buttons/main_menu_races.png", "logs/fresh_adb_frame.png", "1.png"),
]

METHODS = ["rgb", "gray", "gray_blur", "clahe", "down", "edges"]

header = "template".ljust(26) + "".join(m.rjust(13) for m in METHODS)
print(header)
print("-" * len(header))
for name, adb_path, ref in PAIRS:
  template_name = Path(name).name
  template = s.load_template(name)
  adb_img = cv2.cvtColor(cv2.imread(adb_path), cv2.COLOR_BGR2RGB)
  dsk_img = s.to_game_window(
    cv2.cvtColor(cv2.imread(f"references/independent_training/{ref}"), cv2.COLOR_BGR2RGB))
  row = template_name.ljust(26)
  for m in METHODS:
    a = match(adb_img, template, m)
    d = match(dsk_img, template, m)
    row += f"{a:.3f}/{d:.3f}".rjust(13)
  print(row)
print("(each cell: ADB score / desktop score; higher is better on both)")
