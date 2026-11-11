#!/usr/bin/env python3
"""Export the deployed CH4 INT8 graph to official TFLite reference kernels.

No float/dequantized substitute; includes quantized post-ReLU BN MUL/ADD.
Requires the pinned upstream checkouts documented in goal2 preparation.
"""
import csv
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import numpy as np
from tflite_runtime.interpreter import Interpreter, OpResolverType
from tensorflow.lite.python import schema_py_generated as schema

ROOT = Path(__file__).resolve().parents[2]
DEP = ROOT/'AI_Pipeline_amebaPro2/ameba_deployment'
LIB = ROOT/'libraries/VLCInt8Reference'
SRC = LIB/'src'
MODEL = DEP/'vlc_edge_cnn_ch4_int8.tflite'


def qm(value):
    mantissa, shift = math.frexp(value)
    q = int(math.floor(mantissa*(1<<31)+.5))
    if q == 1<<31: q //= 2; shift += 1
    if shift < -31: return 0,0
    return q,shift


def main():
    tf = Path('/tmp/vlc_tf214_cpu_ref')
    gemm = Path('/tmp/vlc_gemmlowp_cpu_ref')
    ruy = Path('/tmp/vlc_ruy_cpu_ref')
    assert subprocess.check_output(['git','-C',str(tf),'rev-parse','HEAD'],text=True).strip() == '4dacf3f368eb7965e9b5c3bbdd5193986081c3b2'
    assert subprocess.check_output(['git','-C',str(gemm),'rev-parse','HEAD'],text=True).strip() == 'e844ffd17118c1e17d94e1ba4354c075a4577b88'
    files = ['tensorflow/lite/kernels/internal/'+p for p in [
        'common.h','common.cc','compatibility.h','cppmath.h','types.h',
        'runtime_shape.h','runtime_shape.cc','optimized/neon_check.h',
        'reference/integer_ops/conv.h','reference/integer_ops/add.h',
        'reference/integer_ops/mul.h','reference/integer_ops/logistic.h']]
    files += ['tensorflow/lite/core/macros.h','tensorflow/lite/kernels/op_macros.h']
    for rel in files:
        dst = SRC/rel; dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(tf/rel,dst)
    for p in (gemm/'fixedpoint').glob('*.h'):
        dst=SRC/'fixedpoint'/p.name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dst)
    dst=SRC/'internal/detect_platform.h';dst.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(gemm/'internal/detect_platform.h',dst)
    dst=SRC/'ruy/profiler/instrumentation.h';dst.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(ruy/'ruy/profiler/instrumentation.h',dst)
    for directory,name in [(tf,'LICENSE.tensorflow'),(gemm,'LICENSE.gemmlowp'),(ruy,'LICENSE.ruy')]:
        (LIB/name).write_bytes(subprocess.check_output(['git','-C',str(directory),'show','HEAD:LICENSE']))
    provenance={str(p.relative_to(SRC)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in SRC.rglob('*') if p.is_file() and p.name not in
                ('vlc_cpu_model.h','vlc_cpu_bank.h','vlc_cpu_nbg.h')}
    binary=MODEL.read_bytes(); model=schema.Model.GetRootAsModel(binary,0);g=model.Subgraphs(0)
    runtime=Interpreter(model_path=str(MODEL),num_threads=1,experimental_preserve_all_tensors=True,
                        experimental_op_resolver_type=OpResolverType.BUILTIN_REF)
    runtime.allocate_tensors()
    ts={t['index']:t for t in runtime.get_tensor_details()}
    def quant(index):
        q=ts[index]['quantization_parameters'];return float(q['scales'][0]),int(q['zero_points'][0])
    text=['#pragma once', '#include <stdint.h>', '#include <string.h>',
        '#include "tensorflow/lite/kernels/internal/reference/integer_ops/conv.h"',
        '#include "tensorflow/lite/kernels/internal/reference/integer_ops/add.h"',
        '#include "tensorflow/lite/kernels/internal/reference/integer_ops/mul.h"',
        '#include "tensorflow/lite/kernels/internal/reference/integer_ops/logistic.h"',
        '// Generated from CH4 TFLite; do not edit.',
        'static const char VLC_CPU_MODEL_SHA256[] = "'+hashlib.sha256(binary).hexdigest()+'";',
        'static int8_t vlcCpuScratch[2][48*48];',
        'static const unsigned VLC_CPU_WORKSPACE_BYTES = sizeof(vlcCpuScratch);']
    emitted=set()
    def array(name,values,ctype):
        a=np.asarray(values).flatten()
        text.append('static const '+ctype+' '+name+'['+str(len(a))+'] = {')
        for start in range(0,len(a),24):text.append(','.join(str(int(v)) for v in a[start:start+24])+',')
        text.append('};')
    def const(index):
        name='vlcTensor'+str(index)
        if index not in emitted:
            array(name,runtime.get_tensor(index),'int32_t' if ts[index]['dtype']==np.int32 else 'int8_t')
            emitted.add(index)
        return name
    def shape(index):return 'tflite::RuntimeShape{'+','.join(map(str,ts[index]['shape']))+'}'
    lines=['inline void vlcCpuInfer(const int8_t* input, int8_t* output) {']
    previous='input';ops=[]
    for k in range(12):
        op=g.Operators(k);code=model.OperatorCodes(op.OpcodeIndex()).BuiltinCode()
        ins=list(op.InputsAsNumpy());out=int(op.Outputs(0));dest='vlcCpuScratch['+str(k%2)+']'
        sin,zin=quant(ins[0]);sout,zout=quant(out)
        lines += ['{']
        if code == schema.BuiltinOperator.CONV_2D:
            opts=schema.Conv2DOptions();v=op.BuiltinOptions();opts.Init(v.Bytes,v.Pos)
            assert opts.StrideH()==opts.StrideW()==opts.DilationHFactor()==opts.DilationWFactor()==1
            w=ts[ins[1]];channels=int(w['shape'][0]);kernel=int(w['shape'][1])
            assert all(w['quantization_parameters']['zero_points']==0)
            mult,shift=zip(*(qm(sin*float(s)/sout) for s in w['quantization_parameters']['scales']))
            array('vlcMult'+str(k),mult,'int32_t');array('vlcShift'+str(k),shift,'int32_t')
            act=opts.FusedActivationFunction();assert act in (schema.ActivationFunctionType.NONE,schema.ActivationFunctionType.RELU)
            amin=max(-128,zout) if act==schema.ActivationFunctionType.RELU else -128
            pad=kernel//2 if opts.Padding()==schema.Padding.SAME else 0
            lines += ['tflite::ConvParams p{};',
                'p.stride_height=p.stride_width=p.dilation_height_factor=p.dilation_width_factor=1;',
                f'p.padding_values.height={pad}; p.input_offset={-zin}; p.output_offset={zout};',
                f'p.quantized_activation_min={amin}; p.quantized_activation_max=127;',
                f'tflite::reference_integer_ops::ConvPerChannel(p,vlcMult{k},vlcShift{k},'+
                f'{shape(ins[0])},{previous},{shape(ins[1])},{const(ins[1])},'+
                f'{shape(ins[2])},{const(ins[2])},{shape(out)},{dest});']
        elif code in (schema.BuiltinOperator.MUL,schema.BuiltinOperator.ADD):
            s2,z2=quant(ins[1]);lines += ['tflite::ArithmeticParams p{};',
                f'p.input1_offset={-zin}; p.input2_offset={-z2}; p.output_offset={zout};',
                'p.quantized_activation_min=-128; p.quantized_activation_max=127;']
            if code==schema.BuiltinOperator.MUL:
                m,s=qm(sin*s2/sout);lines += [f'p.output_multiplier={m}; p.output_shift={s};']
                fn='BroadcastMul4DSlow<int8_t>'
            else:
                twice=2*max(sin,s2);m1,s1=qm(sin/twice);m2,sh2=qm(s2/twice);mo,so=qm(twice/(2**20*sout))
                lines += [f'p.left_shift=20; p.input1_multiplier={m1}; p.input1_shift={s1};',
                          f'p.input2_multiplier={m2}; p.input2_shift={sh2}; p.output_multiplier={mo}; p.output_shift={so};']
                fn='BroadcastAdd4DSlow'
            lines += [f'tflite::reference_integer_ops::{fn}(p,{shape(ins[0])},{previous},'+
                      f'{shape(ins[1])},{const(ins[1])},{shape(out)},{dest});']
        else:
            assert code==schema.BuiltinOperator.LOGISTIC and sout==1/256 and zout==-128
            m,s=qm(sin*(1<<27));radius=int(math.floor(15*(1<<27)/(2**s)))
            lines += [f'tflite::reference_integer_ops::Logistic({zin},{radius},{m},{s},48,{previous},{dest});']
        ops.append(dict(index=k,builtin_code=code,inputs=[int(i) for i in ins],output=out))
        lines+=['}'];previous=dest
    lines+=['memcpy(output,'+previous+',48);','}']
    text+=lines
    (SRC/'vlc_cpu_model.h').write_text('\n'.join(text)+'\n')
    # Fixed, untimed bank initialization. Captured inputs have no claim of TX truth.
    real=ROOT/'dataset/benchmark/npu_raw_pilot_1m_roi106_s01/npu_windows.csv'
    with real.open() as f:rows=list(csv.DictReader(f))
    bank=[np.frombuffer(bytes.fromhex(r['input_int8_hex']),np.int8).copy() for r in rows[:32]]
    cal=np.load(DEP/'calibration_X_ch4_float32.npy').reshape(-1,192)
    bank += [np.clip(np.rint(x*255)-128,-128,127).astype(np.int8) for x in cal[:32]]
    bank=np.stack(bank);expected=[]
    for x in bank:
        runtime.set_tensor(runtime.get_input_details()[0]['index'],x.reshape(1,48,1,4));runtime.invoke()
        expected.append(runtime.get_tensor(runtime.get_output_details()[0]['index']).reshape(48))
    banktext=['#pragma once','#include <stdint.h>',f'static const unsigned VLC_CPU_BANK_N={len(bank)};']
    for name,values,width in [('VLC_CPU_BANK_INPUT',bank,192),('VLC_CPU_BANK_EXPECTED',expected,48)]:
        banktext.append(f'static const int8_t {name}[{len(bank)}][{width}]={{')
        banktext += ['{'+','.join(map(str,np.asarray(x).tolist()))+'},' for x in values];banktext.append('};')
    (SRC/'vlc_cpu_bank.h').write_text('\n'.join(banktext)+'\n')
    nbg_header=ROOT/'RX/Ameba_RX_NPU_Receiver/vlc_edge_cnn_nb.h'
    import re
    nbg=bytes(int(v,16) for v in re.findall(r'0x([0-9a-fA-F]{2})\b',nbg_header.read_text()))
    assert nbg==(DEP/'vlc_edge_cnn_ch4.nb').read_bytes(), 'Benchmark must use the deployed CH4 NBG'
    shutil.copyfile(nbg_header,SRC/'vlc_cpu_nbg.h')
    provenance.update(model_sha256=hashlib.sha256(binary).hexdigest(),operations=ops,
        bank_source=str(real.relative_to(ROOT)),bank_n=len(bank),
        host_reference='tflite_runtime 2.5.0.post1 BUILTIN_REF',
        nbg_sha256=hashlib.sha256(nbg).hexdigest(),
        bank_input_sha256=hashlib.sha256(bank.tobytes()).hexdigest(),
        tensorflow_commit='4dacf3f368eb7965e9b5c3bbdd5193986081c3b2',
        gemmlowp_commit='e844ffd17118c1e17d94e1ba4354c075a4577b88',
        ruy_commit=subprocess.check_output(['git','-C',str(ruy),'rev-parse','HEAD'],text=True).strip())
    (LIB/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    print('Exported',LIB,'bank',len(bank))


if __name__=='__main__':main()
