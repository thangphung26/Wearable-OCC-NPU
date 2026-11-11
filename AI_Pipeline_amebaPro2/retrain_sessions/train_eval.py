#!/usr/bin/env python3
"""Session-isolated retraining and paired offline bit evaluation."""
from session_config import RESULTS, DATASET
import argparse, csv, hashlib, json, math, os, sys, time
from pathlib import Path

import numpy as np
import tensorflow as tf
from scipy.stats import beta

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
from train_model_edge import build_edge_model, export_int8

LR, BATCH, EPOCHS, PATIENCE = 5e-4, 32, 300, 25
SEEDS = [17, 42, 73]


def weights(y):
    ones, zeros = int(np.sum(y == 1)), int(np.sum(y == 0)); n = ones + zeros
    return np.where(y == 1, n / (2 * max(ones, 1)), n / (2 * max(zeros, 1))).astype('float32')


def ci(errors, n):
    return [0.0 if errors == 0 else float(beta.ppf(.025, errors, n-errors+1)),
            1.0 if errors == n else float(beta.ppf(.975, errors+1, n-errors))]


def decode_hex_bits(value):
    return np.array([(int(value, 16) >> b) & 1 for b in range(47, -1, -1)], dtype=np.uint8)


def evaluate(model, X):
    p = model.predict(X, batch_size=256, verbose=0).reshape(-1, 48)
    return (p >= .5).astype(np.uint8)


def tflite_predict(path, Xq):
    it = tf.lite.Interpreter(model_path=str(path)); it.allocate_tensors()
    ii, oi = it.get_input_details()[0], it.get_output_details()[0]
    assert ii['dtype'] == oi['dtype'] == np.int8
    assert ii['shape'].tolist() == [1, 48, 1, 4]
    assert np.isclose(ii['quantization'][0], 1/255) and ii['quantization'][1] == -128
    print(json.dumps({'model': str(path), 'input_quantization': ii['quantization'],
                      'output_quantization': oi['quantization']}), flush=True)
    scale, zero = oi['quantization']
    assert scale > 0
    out=[]
    for q in Xq:
        it.set_tensor(ii['index'], q.reshape(1,48,1,4).astype(np.int8)); it.invoke()
        probability = (it.get_tensor(oi['index']).reshape(48).astype(np.float32) - zero) * scale
        out.append((probability >= .5).astype(np.uint8))
    return np.asarray(out)


def check_agreement(fp, iq, label):
    matched = int(np.sum(fp == iq)); total = int(fp.size)
    result = {'label': label, 'matched_bits': matched, 'n_bits': total, 'agreement': matched / total}
    print(json.dumps(result), flush=True)
    assert result['agreement'] >= .999, result
    return result


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('split',choices=['repeat','loso','lodo']); ap.add_argument('--dataset',default=str(DATASET)); ap.add_argument('--smoke',action='store_true',help='One LOSO fold, seed 42, three epochs under output-root/smoke'); a=ap.parse_args()
    if a.smoke:
        assert a.split == 'loso', '--smoke requires loso'
    epochs = 3 if a.smoke else EPOCHS
    d=np.load(a.dataset); X=d['X']; y=d['y']; sessions=d['session']; uniq=list(dict.fromkeys(sessions.tolist())); dist=d['distance_m']; rep=d['repeat'];
    result=RESULTS/('smoke' if a.smoke else a.split); result.mkdir(parents=True,exist_ok=True)
    started=time.time(); digest=hashlib.sha256(Path(a.dataset).read_bytes()).hexdigest()
    config={'split':a.split,'learning_rate':LR,'batch_size':BATCH,'max_epochs':epochs,'smoke':a.smoke,'early_stopping_patience':PATIENCE,'restore_best_weights':True,'loss':'binary_crossentropy','class_weighting':'inverse frequency, half-total per class (train_model_advanced.bit_sample_weights)','seeds':SEEDS,'validation':'whole sessions from training side; deterministic 10% of eligible sessions, at least one','representative':'training frames only','tensorflow':tf.__version__,'data_sha256':digest,'input_layout':'NHWC (48,1,4), int8 scale 1/255 zero point -128','decision':'TFLite signed output >= 0 maps to bit 1; FP32 probability >= 0.5'}
    config['seeds'] = SEEDS if a.split == 'repeat' else [42]
    config['decision'] = 'p = (int8_output - model_output_zero_point) * model_output_scale; p >= 0.5'
    config['validation'] = 'whole sessions: every tenth eligible session; excluded from fit and calibration'
    (result/'config.json').write_text(json.dumps(config,indent=2)+'\n')
    old = tflite_predict(HERE.parent/'vlc_edge_cnn_int8.tflite', d['input_int8'])
    assert np.mean(old == d['cpu_bits']) >= .9999
    folds=[]
    if a.split=='repeat':
        test_sessions=[s for s in uniq if int(rep[np.flatnonzero(sessions==s)[0]])==3]
        train_sessions=[s for s in uniq if s not in test_sessions]
        val_sessions=[s for i,s in enumerate(train_sessions) if i%10==0]
        folds=[('repeat',train_sessions,val_sessions,test_sessions)]
    elif a.split=='loso':
        for s in uniq:
            tr=[x for x in uniq if x!=s]; val=[x for i,x in enumerate(tr) if i%10==0]; folds.append((s,tr,val,[s]))
    else:
        for held in sorted(set(dist.tolist())):
            test=[s for s in uniq if int(dist[np.flatnonzero(sessions==s)[0]])==held]
            tr=[s for s in uniq if s not in test]; val=[x for i,x in enumerate(tr) if i%10==0]; folds.append((f'{held}m',tr,val,test))
    if a.smoke:
        folds = folds[:1]
    all_rows=[]; histories=[]; repeat_models=[]; agreements=[]
    for fold,train_s,val_s,test_s in folds:
        train_s = [s for s in train_s if s not in val_s]
        assert not set(train_s) & set(val_s)
        assert not set(train_s)&set(test_s) and not set(val_s)&set(test_s)
        ti=np.isin(sessions,train_s); vi=np.isin(sessions,val_s); ei=np.isin(sessions,test_s)
        assert set(sessions[ei]).isdisjoint(set(sessions[ti])|set(sessions[vi]))
        seeds=SEEDS if a.split=='repeat' else [42]
        for seed in seeds:
            fold_started = time.time()
            print(f'START {a.split} {fold} seed={seed}', flush=True)
            tf.keras.utils.set_random_seed(seed)
            m=build_edge_model((48,1,4),LR)
            hist=m.fit(X[ti],y[ti,...,None],sample_weight=weights(y[ti])[:, :, None],validation_data=(X[vi],y[vi,...,None],weights(y[vi])[:,:,None]),epochs=epochs,batch_size=BATCH,callbacks=[tf.keras.callbacks.EarlyStopping(monitor='val_loss',patience=PATIENCE,restore_best_weights=True)],verbose=0)
            h={k:[float(v) for v in vals] for k,vals in hist.history.items()}; histories.append({'fold':fold,'seed':seed,'history':h,'train_sessions':train_s,'validation_sessions':val_s,'test_sessions':test_s})
            if a.split=='repeat':
                repeat_models.append((seed,m,float(min(h['val_loss']))))
            repx=X[ti][:min(200,int(ti.sum()))]
            tfl= result/f'.tmp_{fold}_{seed}.tflite'; export_int8(m,repx,tfl)
            fp=evaluate(m,X[ei]); iq=tflite_predict(tfl,d['input_int8'][ei])
            agreements.append(check_agreement(fp, iq, f'{fold}_{seed}'))
            ids=np.flatnonzero(ei)
            for k,idx in enumerate(ids):
                sid=sessions[idx]; fields={'fold':fold,'seed':seed,'session':sid,'condition':f"{dist[idx]}m_{d['lighting'][idx]}",'distance_m':int(dist[idx]),'lighting':str(d['lighting'][idx]),'repeat':int(rep[idx]),'n_bits':48}
                fields['frame_index'] = int(d['frame_index'][idx])
                for name,bits in [('classic',d['classic_bits'][idx]),('old_int8',d['cpu_bits'][idx]),('new_fp32',fp[k]),('new_int8',iq[k])]:
                    errors=np.flatnonzero(bits!=y[idx]).tolist(); fields[name+'_errors']=len(errors); fields[name+'_windows']=json.dumps(errors,separators=(',',':'))
                all_rows.append(fields)
            histories[-1]['runtime_seconds'] = time.time() - fold_started
            (result/'history_all.json').write_text(json.dumps(histories,indent=2)+'\n')
            print(f'END {fold} seed={seed} epochs={len(h["loss"])} seconds={time.time()-fold_started:.2f}', flush=True)
        if a.split=='repeat':
            ordered=sorted(repeat_models,key=lambda x:x[2]); median=ordered[len(ordered)//2]; seed,m,_=median
            m.save(result/'deployment_candidate.keras'); m.save(result/'deployment_candidate_float32.h5')
            export_int8(m,X[ti][:min(200,int(ti.sum()))],result/'deployment_candidate_int8.tflite')
            repeat_models=[]
    (result/'history_all.json').write_text(json.dumps(histories,indent=2)+'\n')
    columns=list(all_rows[0]);
    with (result/'per_session.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=columns); w.writeheader(); w.writerows(all_rows)
    # Aggregate one seed per session: repeat reports median-seed candidate evaluation rows.
    if a.split=='repeat':
        cand=tf.keras.models.load_model(result/'deployment_candidate.keras'); test=np.isin(sessions,[s for s in uniq if int(rep[np.flatnonzero(sessions==s)[0]])==3]); ids=np.flatnonzero(test)
        fp=evaluate(cand,X[test]); iq=tflite_predict(result/'deployment_candidate_int8.tflite',d['input_int8'][test])
        candidate_agreement = check_agreement(fp, iq, 'deployment_candidate')
        all_rows=[]
        for k,idx in enumerate(ids):
            r={'fold':'repeat','seed':median[0],'session':sessions[idx],'condition':f"{dist[idx]}m_{d['lighting'][idx]}",'distance_m':int(dist[idx]),'lighting':str(d['lighting'][idx]),'repeat':int(rep[idx]),'n_bits':48}
            r['frame_index'] = int(d['frame_index'][idx])
            for name,bits in [('classic',d['classic_bits'][idx]),('old_int8',d['cpu_bits'][idx]),('new_fp32',fp[k]),('new_int8',iq[k])]:
                er=np.flatnonzero(bits!=y[idx]).tolist(); r[name+'_errors']=len(er); r[name+'_windows']=json.dumps(er,separators=(',',':'))
            all_rows.append(r)
        with (result/'per_session.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(all_rows[0])); w.writeheader(); w.writerows(all_rows)
    def summarize(rows):
        out={}
        for label,subset in [('all_sessions',rows),('1_5m',[r for r in rows if r['distance_m']<=5])]:
            n=len(subset)*48; out[label]={}
            for alg in ['classic','old_int8','new_fp32','new_int8']:
                e=sum(r[alg+'_errors'] for r in subset); out[label][alg]={'errors':e,'n_bits':n,'ber':e/n if n else None,'ber_95cp':ci(e,n) if n else None,'exact_windows':sum(r[alg+'_errors']==0 for r in subset)}
        cond={}
        for c in sorted({r['condition'] for r in rows}):
            sub=[r for r in rows if r['condition']==c]; cond[c]={}
            for alg in ['classic','old_int8','new_fp32','new_int8']:
                e=sum(r[alg+'_errors'] for r in sub); n=len(sub)*48; cond[c][alg]={'errors':e,'n_bits':n,'ber':e/n,'ber_95cp':ci(e,n),'exact_windows':sum(r[alg+'_errors']==0 for r in sub)}
        out['per_condition']=cond; return out
    # Exclude duplicate seed rows in non-repeat folds by final metrics from all fold rows as provided.
    summary=summarize(all_rows)
    (result/'summary.json').write_text(json.dumps({'metrics':summary,'runtime_seconds':time.time()-started,'int8_fp32_agreement':agreements,'candidate_agreement':candidate_agreement if a.split=='repeat' else None},indent=2)+'\n')
    print(json.dumps({'split':a.split,'runtime_seconds':time.time()-started,'pooled':summary['all_sessions']},indent=2))


if __name__=='__main__': main()
