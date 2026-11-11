"""Causal timing-assisted delimiter/preamble tracker; no payload or CRC input.

First window acquires within +/-250 ms. Keep optical polarity for a fixed ROI,
then search +/-50 ms around a smoothed timing offset. A change in geometry or
signal polarity requires an explicit reset, not ground-truth-guided relocking.
This is a replay synchronizer, not a blind packet detector.
"""
import math

RULE = dict(version='preamble-tracker-v1', acquisition_radius_ms=250,
            tracking_radius_ms=50, step_ms=5, smoothing=0.25,
            polarity='lock from first delimiter/preamble; no per-frame flip',
            ranking='delimiter/preamble correlation squared only',
            weak_fit_threshold=0.8, weak_fit_action='flag only; retain window')


def sample(values, t):
    p = t / .05 - 1
    k = math.floor(p)
    if k < 0 or k + 1 >= len(values):
        raise ValueError('Outside captured buffer')
    return values[k]*(1-(p-k)) + values[k+1]*(p-k)


def candidates(values, nominal):
    template = [0]*4 + [1,0,0,1,1,0,0,1]
    tm = sum(template)/len(template)
    tv = sum((v-tm)**2 for v in template)
    result = []
    for k in range(-50, 51):
        offset = k*.005
        t = nominal+offset
        try:
            y = [sample(values, t-.35+i*.1) for i in range(12)]
        except ValueError:
            continue
        mean = sum(y)/len(y)
        variance = sum((v-mean)**2 for v in y)
        cross = sum((v-mean)*(b-tm) for v,b in zip(y,template))
        gain = cross/tv
        fit = cross*cross/(variance*tv) if variance > 1e-9 else 0
        result.append(dict(start=t, offset=offset, fit=fit, gain=gain))
    return result


class PreambleTracker:
    def __init__(self):
        self.polarity = None
        self.offset = None
        self.last_nominal = None

    def align(self, values, nominal):
        if self.last_nominal is not None and nominal <= self.last_nominal:
            raise ValueError('Frames must be in increasing time order')
        bank = candidates(values, nominal)
        if not bank:
            raise ValueError('No complete delimiter/preamble')
        acquiring = self.offset is None
        center = 0 if acquiring else self.offset
        eligible = bank if acquiring else [r for r in bank
            if abs(r['offset']-center) <= .05000001 and r['gain']*self.polarity > 0]
        fallback = not eligible
        if fallback:
            # Preserve denominator during loss of signal. Do not pick another
            # phase based on a model output or switch polarity to improve BER.
            chosen = min(bank, key=lambda r: abs(r['offset']-center))
        else:
            chosen = max(eligible, key=lambda r: (r['fit'], -abs(r['offset']-center), -r['offset']))
        if acquiring:
            self.polarity = 1 if chosen['gain'] >= 0 else -1
            self.offset = chosen['offset']
        elif not fallback:
            self.offset += .25*(chosen['offset']-self.offset)
        self.last_nominal = nominal
        return dict(chosen, locked_polarity=self.polarity, filtered_offset_s=self.offset,
                    acquiring=acquiring, fallback=fallback,
                    weak_fit=chosen['fit'] < RULE['weak_fit_threshold'])
