"""Identify each shop capture and measure FP ceilings for the shop anchors."""
import glob
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import scenarios.independent_screens as s

refs = 'references/independent_training'
allrefs = [(os.path.basename(f), cv2.cvtColor(cv2.imread(f), cv2.COLOR_BGR2RGB))
           for f in sorted(glob.glob(f'{refs}/*.png'))]

print('=== identify each shop capture ===')
for n, want in [('1', 'daily_sale'), ('2', 'shop_window'), ('3', 'exchange_confirm'),
                ('4', 'exchange_complete'), ('5', 'shop_window')]:
    live = cv2.cvtColor(cv2.imread(f'{refs}/shop{n}.png'), cv2.COLOR_BGR2RGB)
    r = s.identify_screen(live)
    mark = 'OK' if r.screen == want else 'MISMATCH'
    print(f'  shop{n}: {r.screen} ({r.score:.3f}) {mark}')

print('=== FP ceilings (worst non-own-screen reference) ===')
for name, own in [('shop_select_all', ('shop2.png', 'shop5.png')),
                  ('shop_confirm_title', ('shop3.png',)),
                  ('shop_complete_title', ('shop4.png',)),
                  ('shop_exchanged_body', ('shop4.png',))]:
    t = cv2.cvtColor(cv2.imread(f'assets/independent/{name}.png'), cv2.COLOR_RGB2GRAY)
    worst, wn = 0.0, ''
    for rn, rimg in allrefs:
        if rn in own:
            continue
        sc = cv2.matchTemplate(cv2.cvtColor(rimg, cv2.COLOR_RGB2GRAY), t,
                               cv2.TM_CCOEFF_NORMED).max()
        if sc > worst:
            worst, wn = sc, rn
    print(f'  {name:<22} worst {worst:.3f} ({wn})')
