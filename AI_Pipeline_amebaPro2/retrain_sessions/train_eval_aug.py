#!/usr/bin/env python3
"""Session-isolated CPU training with raw-window augmentation before quantization."""
from session_config import RESULTS, DATASET
import os
os.environ['CUDA_VISIBLE_DEVICES'] = ''
os.environ.setdefault('TF_NUM_INTRAOP_THREADS', '1')
os.environ.setdefault('TF_NUM_INTEROP_THREADS', '1')
import argparse
import csv
import hashlib
import json
import time

import numpy as np
import tensorflow as tf
from train_eval import (HERE, ROOT, LR, BATCH, EPOCHS, PATIENCE, SEEDS, weights,
                        ci, build_edge_model, export_int8, evaluate, tflite_predict, check_agreement)
from robustness import normalize


def quantize(windows):
    """Exact benchmark_precrc.normalize arithmetic, batched over bit quadruplets."""
    v = np.asarray(windows, dtype=np.float64).reshape(-1,48,4)
    lo = v.min(axis=-1,keepdims=True)
    span = v.max(axis=-1,keepdims=True)-lo
    q = np.floor((v-lo)/np.where(span > .5, span, 1)*255+.5)-128
    return np.where(span > .5, np.clip(q,-128,127), -128).astype(np.int8)


class AugmentedBatches(tf.keras.utils.PyDataset):
    def __init__(self, windows, sigma, y, seed):
        super().__init__(workers=1, use_multiprocessing=False, max_queue_size=1)
        self.windows, self.sigma, self.y = windows, sigma, y
        self.seed, self.epoch = seed, 0
        self.w = weights(y)[:,:,None]
        self.order = np.random.default_rng(seed).permutation(len(y))

    def __len__(self):
        return (len(self.y)+BATCH-1)//BATCH

    def __getitem__(self, batch):
        ids = self.order[batch*BATCH:(batch+1)*BATCH]
        rng = np.random.default_rng([self.seed,self.epoch,batch])
        n = len(ids)
        offsets = np.where(rng.random(n)<.5, rng.choice([0,1,2,3,5,6,7,8],n), 4)
        v = self.windows[ids,offsets].copy()
        halves = v.reshape(n,48,2,2).mean(axis=-1)
        amplitude = np.sqrt(np.mean(((halves[:,:,0]-halves[:,:,1])/2)**2,axis=1))
        contrast = rng.random(n)<.3
        alpha = np.where(contrast,rng.uniform(.25,1,n),1)
        mean = v.mean(axis=1,keepdims=True)
        v = mean+alpha[:,None]*(v-mean)
        v += rng.normal(size=v.shape)*(self.sigma[ids]*contrast)[:,None]
        noise = rng.random(n)<.3
        snr = rng.uniform(3,20,n)
        v += rng.normal(size=v.shape)*(amplitude*10**(-snr/20)*noise)[:,None]
        X = ((quantize(v).astype(np.float32)+128)/255).reshape(n,48,1,4)
        return X, self.y[ids,...,None], self.w[ids]

    def on_epoch_end(self):
        self.epoch += 1
        self.order = np.random.default_rng([self.seed,self.epoch]).permutation(len(self.y))


def summarize(rows):
    def metrics(sub):
        n = len(sub)*48
        return {alg: dict(errors=(e:=sum(r[alg+'_errors'] for r in sub)), n_bits=n,
                          ber=e/n if n else None, ber_95cp=ci(e,n) if n else None,
                          exact_windows=sum(r[alg+'_errors']==0 for r in sub))
                for alg in ['classic','old_int8','new_fp32','new_int8']}
    return dict(all_sessions=metrics(rows), **{'1_5m':metrics([r for r in rows if r['distance_m']<=5])},
                per_condition={c:metrics([r for r in rows if r['condition']==c]) for c in sorted({r['condition'] for r in rows})})


def lodo_tests(sessions, distances):
    uniq = list(dict.fromkeys(sessions.tolist()))
    assert len(uniq) == 24
    assert sorted(set(distances.tolist())) == [1,3,5,7]
    assert all(len(set(distances[sessions == s])) == 1 for s in uniq)
    return [(f'{held}m', [s for s in uniq if int(distances[np.flatnonzero(sessions == s)[0]]) == held])
            for held in sorted(set(distances.tolist()))]


def assert_lodo_isolation(distances, ti, vi, ei, calibration_ids):
    held = set(distances[ei].tolist())
    assert len(held) == 1, 'LODO test must contain exactly one distance'
    assert np.array_equal(ei, np.isin(distances, list(held))), 'LODO must test every held-distance window'
    assert held.isdisjoint(distances[ti]), 'Held distance leaked into training'
    assert held.isdisjoint(distances[vi]), 'Held distance leaked into validation'
    assert held.isdisjoint(distances[calibration_ids]), 'Held distance leaked into calibration'
    assert np.all(ti[calibration_ids]), 'Calibration must use training windows only'
    assert not np.any(ti & vi), 'Training and validation overlap'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('split', choices=['repeat','loso','lodo'])
    parser.add_argument('--smoke', action='store_true', help='One LODO fold under output-root/smoke_lodo_aug')
    parser.add_argument('--smoke-epochs', type=int, default=3, help='Epoch cap for --smoke (default: 3)')
    args = parser.parse_args()
    if args.smoke:
        assert args.split == 'lodo', '--smoke requires lodo'
        assert 0 < args.smoke_epochs <= EPOCHS
    epochs = args.smoke_epochs if args.smoke else EPOCHS
    result = RESULTS/('smoke_lodo_aug' if args.smoke else f'{args.split}_aug')
    if args.split == 'lodo':
        result.mkdir(parents=True, exist_ok=False)
    started = time.time()
    dataset = DATASET
    cache = dataset.with_name('windows_offsets.npz')
    d, offsets = np.load(dataset), np.load(cache)
    X,y,sessions = d['X'],d['y'],d['session']
    assert np.array_equal(sessions,offsets['session'])
    assert np.array_equal(d['frame_index'],offsets['frame_id'])
    assert np.array_equal(y,offsets['truth'])
    assert np.array_equal(offsets['offsets_ms'],np.arange(-40,41,10))
    windows,sigma = offsets['windows'],offsets['noise_std']
    q = quantize(windows[:,4]).reshape(-1,192)
    reference = np.stack([np.frombuffer(normalize(w),dtype=np.int8) for w in windows[:,4]])
    assert np.array_equal(q,reference)
    assert np.array_equal(q,d['input_int8'])
    if args.split != 'lodo':
        result.mkdir(parents=True,exist_ok=True)
    config = json.loads((RESULTS/args.split/'config.json').read_text())
    config.update(split=f'{args.split}_aug', tensorflow_device='CPU',
                  data_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(),
                  offsets_sha256=hashlib.sha256(cache.read_bytes()).hexdigest(),
                  augmentation=dict(timing_probability=.5, offsets_nonzero_ms=[-40,-30,-20,-10,10,20,30,40],
                                    contrast_probability=.3, contrast_uniform=[.25,1], noise='session residual std',
                                    snr_probability=.3, snr_uniform_db=[3,20],
                                    order='timing, contrast plus session noise, SNR AWGN, normalize, int8, dequantize',
                                    independence='independent Bernoulli per window; perturbations can compose',
                                    snr_reference='half-bit RMS amplitude of timing-selected window before added perturbations'),
                  calibration='first 200 unperturbed training-only windows, identical baseline contract',
                  rates=dict(acquisition_fps=30,resampling_hz=20,link_bit_s=5))
    if args.split == 'lodo':
        config.update(max_epochs=epochs, smoke=args.smoke, seeds=[42])
    (result/'config.json').write_text(json.dumps(config,indent=2)+'\n')
    old=tflite_predict(HERE.parent/'vlc_edge_cnn_int8.tflite',d['input_int8'])
    assert np.mean(old==d['cpu_bits']) >= .9999
    uniq=list(dict.fromkeys(sessions.tolist()))
    tests = [[s for s in uniq if d['repeat'][np.flatnonzero(sessions==s)[0]]==3]] if args.split=='repeat' else [[s] for s in uniq]
    folds = [(('repeat' if args.split=='repeat' else test_s[0]), test_s) for test_s in tests]
    if args.split == 'lodo':
        folds = lodo_tests(sessions, d['distance_m'])
        if args.smoke:
            folds = folds[:1]
    histories, rows, agreements, candidates = [], [], [], []
    for fold,test_s in folds:
        eligible=[s for s in uniq if s not in test_s]
        val_s=eligible[::10]
        train_s=[s for s in eligible if s not in val_s]
        assert set(test_s).isdisjoint(set(train_s)|set(val_s))
        assert set(train_s).isdisjoint(val_s)
        ti,vi,ei = [np.isin(sessions,s) for s in (train_s,val_s,test_s)]
        calibration_ids = np.flatnonzero(ti)[:200]
        if args.split == 'lodo':
            assert_lodo_isolation(d['distance_m'], ti, vi, ei, calibration_ids)
            print(f'LODO isolation PASS {fold}: train={int(ti.sum())} validation={int(vi.sum())} '
                  f'test={int(ei.sum())} calibration={len(calibration_ids)}', flush=True)
        for seed in (SEEDS if args.split=='repeat' else [42]):
            tf.keras.backend.clear_session()
            tf.keras.utils.set_random_seed(seed)
            tick=time.time()
            print(f'START {args.split}_aug {fold} seed={seed}',flush=True)
            model=build_edge_model((48,1,4),LR)
            batches=AugmentedBatches(windows[ti],sigma[ti],y[ti],seed)
            history=model.fit(batches,validation_data=(X[vi],y[vi,...,None],weights(y[vi])[:,:,None]),
                              epochs=epochs,callbacks=[tf.keras.callbacks.EarlyStopping(monitor='val_loss',patience=PATIENCE,restore_best_weights=True)],verbose=0)
            h={k:[float(v) for v in values] for k,values in history.history.items()}
            histories.append(dict(fold=fold,seed=seed,history=h,train_sessions=train_s,validation_sessions=val_s,
                                  calibration_sessions=train_s,test_sessions=test_s,best_epoch=int(np.argmin(h['val_loss']))+1,
                                  runtime_seconds=time.time()-tick))
            path=result/f'.tmp_{fold}_{seed}.tflite'
            assert set(sessions[ti]).isdisjoint(sessions[ei])
            if args.split == 'lodo':
                assert_lodo_isolation(d['distance_m'], ti, vi, ei, calibration_ids)
            export_int8(model,X[calibration_ids],path)
            fp,iq=evaluate(model,X[ei]),tflite_predict(path,d['input_int8'][ei])
            agreements.append(check_agreement(fp,iq,f'{fold}_{seed}'))
            seed_rows=[]
            for k,idx in enumerate(np.flatnonzero(ei)):
                r=dict(fold=fold,seed=seed,session=str(sessions[idx]),condition=f"{d['distance_m'][idx]}m_{d['lighting'][idx]}",
                       distance_m=int(d['distance_m'][idx]),lighting=str(d['lighting'][idx]),repeat=int(d['repeat'][idx]),
                       n_bits=48,frame_index=int(d['frame_index'][idx]))
                for name,bits in [('classic',d['classic_bits'][idx]),('old_int8',d['cpu_bits'][idx]),('new_fp32',fp[k]),('new_int8',iq[k])]:
                    errors=np.flatnonzero(bits!=y[idx]).tolist()
                    r[name+'_errors'],r[name+'_windows']=len(errors),json.dumps(errors,separators=(',',':'))
                seed_rows.append(r)
            if args.split=='repeat':
                model.save(result/f'seed_{seed}.keras')
                candidates.append((min(h['val_loss']),seed,seed_rows))
            else:
                rows.extend(seed_rows)
            (result/'history_all.json').write_text(json.dumps(histories,indent=2)+'\n')
            print(f'END {fold} seed={seed} epochs={len(h["loss"])} seconds={time.time()-tick:.2f}',flush=True)
        if args.split=='loso':
            write_results(result,rows,histories,agreements,started,complete=len(histories)==24)
        elif args.split=='lodo':
            write_results(result,rows,histories,agreements,started,complete=not args.smoke and len(histories)==4)
    candidate_agreement=None
    if args.split=='repeat':
        _,seed,rows=sorted(candidates,key=lambda c:c[0])[1]
        model=tf.keras.models.load_model(result/f'seed_{seed}.keras')
        model.save(result/'deployment_candidate_aug.keras')
        model.save(result/'deployment_candidate_aug_float32.h5')
        path=result/'deployment_candidate_aug_int8.tflite'
        export_int8(model,X[ti][:200],path)
        fp,iq=evaluate(model,X[ei]),tflite_predict(path,d['input_int8'][ei])
        candidate_agreement=check_agreement(fp,iq,'deployment_candidate_aug')
        assert all(r['new_int8_errors']==int(np.sum(iq[k]!=y[idx])) for k,(r,idx) in enumerate(zip(rows,np.flatnonzero(ei))))
    write_results(result,rows,histories,agreements,started,not args.smoke,candidate_agreement)


def write_results(result,rows,histories,agreements,started,complete,candidate_agreement=None):
    with (result/'per_session.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary=dict(metrics=summarize(rows),runtime_seconds=time.time()-started,complete=complete,
                 completed_folds=len({h['fold'] for h in histories}),int8_fp32_agreement=agreements,
                 candidate_agreement=candidate_agreement,
                 best_epochs=[dict(fold=h['fold'],seed=h['seed'],best_epoch=h['best_epoch']) for h in histories])
    if all(h['fold'] in ('1m','3m','5m','7m') for h in histories):
        summary['metrics']['per_fold'] = {fold:summarize([r for r in rows if r['fold']==fold])['all_sessions']
                                          for fold in dict.fromkeys(h['fold'] for h in histories)}
    (result/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(dict(output=str(result),complete=complete,pooled=summary['metrics']['all_sessions'])),flush=True)


if __name__=='__main__':
    main()
