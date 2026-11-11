#!/usr/bin/env python3
"""Compare logged ON/OFF camera images; diagnostic only, no receiver changes."""
import hashlib
import json
from pathlib import Path
import cv2
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'dataset/benchmark/controlled_led_snapshots_gray8v2_5m_20261025_s01'


def main():
    s=json.loads((OUT/'summary.json').read_text());assert s['complete']
    groups={0:[],1:[]};hashes={}
    for r in s['shots']:
        p=OUT/r['file'];hashes[r['file']]=hashlib.sha256(p.read_bytes()).hexdigest()
        im=cv2.imread(str(p),cv2.IMREAD_GRAYSCALE)
        assert im.shape==(288,352)
        groups[r['commanded_on']].append(im.astype(float))
    assert len(groups[0])==len(groups[1])==3
    on=np.mean(groups[1],axis=0);off=np.mean(groups[0],axis=0)
    results={}
    for name,roi in [('selected_roi',[107,60,69,65]),('core_diagnostic_only',[133,82,25,28])]:
        x,y,w,h=roi;a=on[y:y+h,x:x+w];b=off[y:y+h,x:x+w];d=a-b
        results[name]=dict(roi=roi,on_mean=float(a.mean()),off_mean=float(b.mean()),
            difference=float(d.mean()),pixels=int(d.size),
            positive_pixels=int(np.count_nonzero(d>0)),
            strong_positive_pixels=int(np.count_nonzero(d>20)),
            negative_pixels=int(np.count_nonzero(d<0)),
            negative_mean_difference=float(d[d<0].mean()) if np.any(d<0) else None)
    (OUT/'spatial_analysis.json').write_text(json.dumps(dict(results=results,source_sha256=hashes,
        limitations=['Timing-assisted labels over SSH, not hardware-triggered exposures.',
            'Core is an illustrative region chosen from these diagnostic images, not an independently validated ROI policy.',
            'No model/CRC/payload timing ranking, BER rescoring or RX ROI update performed.']),indent=2)+'\n')
    lines=['# Controlled LED spatial check at 5 m','',
        'Current runtime: corrected JPEG grayscale, exposure 2000 us, gain 256.',
        'TX commanded 1-s ON/OFF plateaus. Three ON and three OFF JPEGs saved unchanged.',
        'Each snapshot header arrived approximately 0.61-0.66 s after its logged level. SSH timing is approximate.', '',
        '| Region | Coordinates | ON mean | OFF mean | ON - OFF |',
        '|---|---|---:|---:|---:|']
    for name,r in results.items():lines.append(f'| {name} | {r["roi"]} | {r["on_mean"]:.2f} | {r["off_mean"]:.2f} | {r["difference"]:.2f} |')
    lines+=['', '## Findings', '',
        '- The LED is inside the selected ROI; identical ROI headers in all six snapshots show no automatic ROI jump.',
        '- The selected rectangle mixes a bright LED core and image regions that darken when LED is ON. Their contributions largely cancel in the simple mean.',
        '- The illustrative central core has a large positive ON/OFF difference. Its current clipping does not eliminate all useful modulation.',
        '- Many surrounding ROI pixels darken in ON images. ISP/tone response, optical effects or other scene changes are possible; no unique physical cause is established.',
        '- JPEG dimensions are 352x288. SDK VideoSetting clamps JPEG requests below this size, despite the application requesting 320x192. The existing ROI is inside both bounds, so resolution mismatch alone does not prove the coordinates are wrong.',
        '- The corrected callback reads genuine grayscale, but a uniform ROI average is still a poor feature for this particular geometry. The 1-s check bypasses frame synchronization and CNN entirely.', '',
        '## Proposed next step (not performed)', '',
        'Keep 2 ms/gain 256. Evaluate a tighter LED-core ROI or a documented spatial weighting/mask based on modulation rather than full-rectangle average. Freeze the spatial rule before a fresh 120-s test. The diagnostic core is not claimed optimal or applied automatically.',
        'Keep old pilot BER unchanged. Do not report this spatial diagnostic as improved BER or production PDR.', '',
        '![Commanded ON](01_tx_on.jpg)', '', '![Commanded OFF](04_tx_off.jpg)', '']
    (OUT/'REPORT.md').write_text('\n'.join(lines))
    print(json.dumps(results,indent=2));print(OUT/'REPORT.md')


if __name__=='__main__':main()
