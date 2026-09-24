"""Capture live reference screenshots from an ADB-connected emulator.

Saves full native 800x1080 frames into references/independent_training_adb/ for use with
offline replay validation (devtools/replay_independent_screens.py --source adb).

Usage:
  py devtools/capture_adb_references.py                      # Interactive capture mode
  py devtools/capture_adb_references.py --device 127.0.0.1:5555
  py devtools/capture_adb_references.py --name home.png       # One-shot named capture
"""

import argparse
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.bot as bot
from utils.adb_actions import init_adb, screenshot


OUT_DIR = "references/independent_training_adb"


def capture_frame(device_id=None):
    if device_id:
        bot.device_id = device_id
    bot.use_adb = True

    if not init_adb():
        print(f"[ERROR] Could not connect to ADB device '{bot.device_id}'.")
        return None

    img = screenshot()
    if img is None or img.size == 0:
        print("[ERROR] Captured frame is empty.")
        return None

    return img


def save_frame(img, filename):
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, filename)
    if not filename.lower().endswith(".png"):
        out_path += ".png"

    # Convert RGB to BGR for saving
    bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    cv2.imwrite(out_path, bgr)
    print(f"[OK] Saved {img.shape[1]}x{img.shape[0]} capture -> {out_path}")
    return out_path


def interactive_mode():
    print("=" * 60)
    print("ADB Reference Capture Tool")
    print("Captures will be saved to:", OUT_DIR)
    print("Press Enter to take a capture, or type a filename, or 'q' to quit.")
    print("=" * 60)

    count = 1
    while True:
        entry = input(f"\n[Capture #{count}] Enter filename (or Enter for adb_{count}.png): ").strip()
        if entry.lower() in ("q", "quit", "exit"):
            break

        filename = entry if entry else f"adb_{count}.png"
        img = capture_frame()
        if img is not None:
            save_frame(img, filename)
            count += 1


def main():
    parser = argparse.ArgumentParser(description="Capture ADB emulator reference screenshots.")
    parser.add_argument("--device", type=str, default="127.0.0.1:5555", help="ADB device address:port")
    parser.add_argument("--name", type=str, default=None, help="One-shot capture filename")
    args = parser.parse_args()

    bot.device_id = args.device
    bot.use_adb = True

    if args.name:
        img = capture_frame(args.device)
        if img is not None:
            save_frame(img, args.name)
    else:
        interactive_mode()


if __name__ == "__main__":
    main()
