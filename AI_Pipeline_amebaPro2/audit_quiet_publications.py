#!/usr/bin/env python3
"""Audit actual published 48-bit hex against all recorded TX frames.

Full-payload membership is used to diagnose unknown/duplicate records, not
to pick a favorable reference for BER. No raw-window BER is estimated.
"""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import re

TX=re.compile(r'\[TX_GT\] frame_id=(\d+).*?frame_hex=(0x[0-9A-Fa-f]+).*?tx_us=(\d+)')
RX=re.compile(r'\[TELEMETRY:([^\]]+)\]\s+(\{.*\})')


def crc_ok(frame):
    crc=0
    for shift in range(47,7,-1):
        feedback=((crc>>7)&1)^((frame>>shift)&1)
        crc=(crc<<1)&255
        if feedback:crc^=7
    return crc==(frame&255)


def audit(path):
    events=[json.loads(line) for line in (path/'events.jsonl').read_text().splitlines()]
    tx=[]; outputs=[];begin=end=None
    for e in events:
        line=e['line'];t=e['host_mono_ns']/1e9
        m=TX.search(line) if e['source']=='tx' else None
        if m:tx.append(dict(t=t,frame_id=int(m[1]),hex=int(m[2],16),test='payload_mode=test' in line))
        if e['source']=='rx':
            if line=='[PERF] RESET':begin=t
            if line.startswith('[EDGE_PERF] '):end=t
            m=RX.search(line)
            if m:
                payload=json.loads(m[2]);outputs.append(dict(t=t,decoder=m[1],payload=payload,hex=int(payload['frame_hex'],16)))
    if begin is None or end is None:raise ValueError('No completed measurement interval')
    tests=[r for r in tx if r['test'] and begin<=r['t'] and r['t']+11.1<=end]
    expected=json.loads((path/'metadata.json').read_text())['frames']
    if len(tests)!=expected or len({r['hex'] for r in tests})!=expected:
        raise ValueError('Incomplete or non-unique test ground truth')
    # Check independent host-clock stability; precise exposure timing is not available.
    test_spacing=[b['t']-a['t'] for a,b in zip(tests,tests[1:])]
    audit_rows=[];seen=Counter();time_residuals=[]
    for output in outputs:
        if not begin<=output['t']<=end:continue
        frame=output['hex']
        candidates=[r for r in tx if r['hex']==frame]
        match=min(candidates,key=lambda r:abs(output['t']-r['t']-9.6)) if candidates else None
        nearest=min(tests,key=lambda r:abs(output['t']-r['t']-9.6))
        delta=output['t']-nearest['t']-9.6
        if match is None:
            classification=('never_transmitted_in_recording' if
                tests[0]['t']+9.6-1.5 <= output['t'] <= tests[-1]['t']+9.6+1.5
                else 'unknown_boundary_unverifiable')
        elif not match['test']:
            classification='boundary_sensor_frame'
        elif abs(output['t']-match['t']-9.6)>1.5:
            classification='test_payload_outside_original_tolerance'
        else:
            classification='duplicate_correct' if seen[match['frame_id']] else 'first_correct'
            seen[match['frame_id']]+=1
            time_residuals.append(output['t']-match['t']-9.6)
        row=dict(rx_time_s=output['t']-begin,decoder=output['decoder'],
            frame_hex=f'0x{frame:012X}',classification=classification,
            exact_tx_frame_id=match['frame_id'] if match else '',
            exact_time_residual_s=output['t']-match['t']-9.6 if match else '',
            nearest_time_tx_frame_id=nearest['frame_id'],nearest_time_residual_s=delta,
            original_bucket='matched' if abs(delta)<=1.5 else 'unmatched',
            patient=(frame>>40)&15,seq=(frame>>8)&15,
            preamble_ok=(frame>>44)==10,crc_ok=crc_ok(frame))
        audit_rows.append(row)
    unknown=[r for r in audit_rows if r['classification']=='never_transmitted_in_recording']
    unknown_ids=Counter(str(r['patient']) for r in unknown)
    by_decoder={d:dict(Counter(r['classification'] for r in audit_rows if r['decoder']==d)) for d in sorted({r['decoder'] for r in audit_rows})}
    tolerance={}
    for width in [.5,1,1.5,2,3,5]:
        correct={r['exact_tx_frame_id'] for r in audit_rows if r['exact_tx_frame_id'] in {x['frame_id'] for x in tests}
                 and abs(r['exact_time_residual_s'])<=width}
        tolerance[str(width)]=len(correct)
    # Offline patient-ID filter sensitivity only. It was NOT active during acquisition.
    remaining=[r for r in unknown if r['patient']==1]
    summary=dict(session=path.name,test_frames=len(tests),publications_in_measurement=len(audit_rows),
        classifications=dict(Counter(r['classification'] for r in audit_rows)),
        unique_test_frames_received=len(seen),
        missed_tx_frame_ids=[r['frame_id'] for r in tests if not seen[r['frame_id']]],
        unknown_by_original_bucket=dict(Counter(r['original_bucket'] for r in unknown)),
        unknown_by_decoder=dict(Counter(r['decoder'] for r in unknown)),
        unknown_by_patient=unknown_ids,by_decoder=by_decoder,
        all_published_preamble_crc_valid=all(r['crc_ok'] and r['preamble_ok'] for r in audit_rows),
        test_start_interval_min_s=min(test_spacing) if test_spacing else None,
        test_start_interval_max_s=max(test_spacing) if test_spacing else None,
        correct_completion_residual_min_s=min(time_residuals) if time_residuals else None,
        correct_completion_residual_max_s=max(time_residuals) if time_residuals else None,
        correct_unique_frames_by_time_tolerance_s=tolerance,
        offline_patient1_filter=dict(unknown_removed=len(unknown)-len(remaining),unknown_remaining=len(remaining),
            caveat='Counterfactual diagnostic only; no actual firmware filtering was applied. Unknown patient-1 frames remain.'),
        caveats=['Unknown means no exact match to any recorded TX_GT, including sensor frames outside the test.',
                 'Unknown outputs outside the fully observed test-completion interval are boundary-unverifiable, not counted as proven false publications.',
                 'ID and sequence cannot establish correctness; actual 48-bit frame_hex was audited.',
                 'An unknown frame may be an off-boundary mixture, noise candidate or CRC collision; raw signals are unavailable to distinguish causes.',
                 'NPU and classic publication labels are within a hybrid sharing timing search and state.',
                 'No full-window BER. No firmware or acquisition logs changed.'])
    with (path/'publication_audit.csv').open('w',newline='') as f:
        fields=list(audit_rows[0]) if audit_rows else ['rx_time_s','decoder','frame_hex','classification']
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(audit_rows)
    (path/'publication_audit_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('sessions',nargs='+',type=Path);a=p.parse_args()
    for path in a.sessions:
        summary=audit(path)
        counts=summary['classifications']
        dec=summary['unknown_by_decoder']
        md=['# Published-packet audit','',
            '| Classification | Count |','|---|---:|']
        for name,count in counts.items():md.append(f'| {name} | {count} |')
        md += ['',f"Distinct correct test frames: {summary['unique_test_frames_received']}/{summary['test_frames']}.",
            f"False interior publications labelled classic: {dec.get('CLASSIC_PLL',0)+dec.get('CLASSIC_PLL_INV',0)}; "
            f"labelled NPU: {dec.get('NPU',0)+dec.get('NPU_INV',0)}.",
            f"All audited outputs pass preamble/CRC: {summary['all_published_preamble_crc_valid']}.",
            f"Delivered counts by matching tolerance (seconds): {summary['correct_unique_frames_by_time_tolerance_s']}.",
            'Unknown boundary records are excluded from proven false-publication counts.', '',
            '## Firmware findings', '',
            'validateAndPublish accepts the first preamble/CRC/range-valid candidate immediately.',
            'Classic scans 9 rates x 4 phases x 2 polarities at each resampled time step.',
            'This tests many overlapping hypotheses; the short CRC does not uniquely identify a true frame boundary.',
            'The duplicate guard stores only the last published full frame, so an intervening false packet',
            'can allow the preceding correct packet to be published again.',
            'The logs establish false acceptance, but do not identify the exact physical/noise cause.', '',
            '## Next implementation priorities', '',
            '1. Establish frame-boundary/delimiter evidence and Manchester transition confidence before publication.',
            '2. Store a bounded recent-frame cache per sender, rather than only the last full frame.',
            '3. Use an explicitly configured patient/node whitelist for a known single-TX deployment.',
            f"   Offline ID=1 filtering would still leave {summary['offline_patient1_filter']['unknown_remaining']} false records in this session.",
            '4. Evaluate stronger CRC/framing as a separately versioned protocol if needed; quantify rate/latency costs.',
            'These are prospective fixes. No acquisition data, firmware or reported online counts were changed.',
            'Do not tune to the synthetic HR-index pattern, or use physiology plausibility to claim channel correctness.', '',
            'See publication_audit.csv and publication_audit_summary.json for reproducible per-output evidence.']
        (path/'PUBLICATION_AUDIT.md').write_text('\n'.join(md)+'\n')
        reports=json.loads((path/'reports.json').read_text()) if (path/'reports.json').exists() else []
        if any(r.get('frame_guard') for r in reports):
            # Legacy firmware commentary above must not be attributed to guarded runs.
            prefix=md[:md.index('## Firmware findings')]
            prefix += ['## Guarded firmware', '',
                'The captured performance report identifies a frame-evidence guard and recent-frame cache.',
                'Counts above are measured publications, not a prediction of guard effectiveness.',
                'Decoder labels are attribution within hybrid operation; full-window BER remains unavailable.']
            (path/'PUBLICATION_AUDIT.md').write_text('\n'.join(prefix)+'\n')
        print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
