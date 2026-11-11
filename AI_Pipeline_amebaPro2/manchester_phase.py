"""Local phase refinement using Manchester half-bit consistency only.

No labels, CRC, model outputs or reference payload enter this API. Coarse
frame timing must already exist; this does not resolve whole-bit ambiguity.
"""
from preamble_tracker import sample

RULE=dict(version='manchester-phase-v1',radius_ms=25,step_ms=5,
    samples_per_bit=4,ranking='half-bit contrast energy / within-bit centered energy',
    tie_break='smallest absolute correction, then earlier time',
    zero_energy_action='score zero, retain window')


def consistency(window):
    if len(window)!=192:raise ValueError('Expected 48 bits x 4 samples')
    contrast=0.;energy=0.
    for j in range(0,192,4):
        v=window[j:j+4];mean=sum(v)/4
        contrast+=((v[0]+v[1]-v[2]-v[3])/2)**2
        energy+=sum((x-mean)**2 for x in v)
    return min(1.,contrast/energy) if energy>1e-9 else 0.


def refine(values,coarse_start):
    bank=[]
    for step in range(-5,6):
        delta=step*.005;t=coarse_start+delta
        try:w=[sample(values,t+(i+.5)*.05) for i in range(192)]
        except ValueError:continue
        bank.append((consistency(w),-abs(step),-step,t,delta,w))
    if not bank:raise ValueError('No complete Manchester window')
    score,_,_,t,delta,window=max(bank)
    return dict(start=t,correction_ms=delta*1000,consistency=score,
                boundary_hit=abs(delta)>=.0249999),window
