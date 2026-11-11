"""Executed inside Acuity 6.18.8. All outputs confined to /work."""
import json
import glob
import os
from pathlib import Path
import re
import shutil
import subprocess

stem=json.loads(Path('manifest.json').read_text()).get('nbg_stem', 'vlc_edge_cnn_aug')
nbg_name=stem+'.nb'
meta_name=stem+'_nbg_meta.json'

acuity=Path(os.environ.get('ACUITY_PATH','/opt/acuity/acuity-toolkit/bin'))
peg=acuity/'pegasus'
if peg.exists():
    command=[str(peg)]
else:
    candidates=glob.glob('/opt/acuity/**/pegasus.py',recursive=True)
    if len(candidates)!=1:
        raise RuntimeError('Cannot locate unique pegasus.py: '+repr(candidates))
    command=['python3',candidates[0]]

def run(*args):
    subprocess.run(command+list(args),check=True)

if all(Path(name).exists() for name in
       ('aug.json','aug.data','aug.quantize','post_generated.yml')):
    print('reusing aug.json, aug.data, aug.quantize, post_generated.yml')
else:
    run('import','tflite','--model','/model/deployment_candidate_aug_int8.tflite',
        '--output-model','aug.json','--output-data','aug.data')
    assert Path('aug.quantize').exists(), 'TFLite import must preserve original INT8 quantization'
    # Discover the output layer ID instead of assuming the CH4 output_21 name.
    run('generate','postprocess-file','--model','aug.json',
        '--postprocess-file-output','post_generated.yml')
post=Path('post_generated.yml').read_text()
for required in ('dump_results','file_type: TENSOR',
                 'add_postproc_node: false','force_float32: true'):
    assert required in post, 'Missing postprocess setting: '+required
lids=re.findall(r'^\s*- lid:\s*([^\n]*)',post,re.MULTILINE)
assert len(lids)==1, ('Expected exactly one - lid: entry',lids)
print('Output lid:',lids[0].strip())
Path('post.yml').write_text(post)
if Path(nbg_name).exists():
    print('reusing '+nbg_name)
else:
    if not Path('wksp_nbg_unify/network_binary.nb').exists():
        run('export','ovxlib','--model','aug.json','--model-data','aug.data',
            '--dtype','quantized','--model-quantize','aug.quantize','--target-ide-project','linux64',
            '--with-input-meta','input.yml','--output-path','wksp/aug',
            '--optimize','VIP8000NANONI_PID0XAD','--pack-nbg-unify',
            '--viv-sdk','/opt/acuity/Vivante_IDE/VivanteIDE5.8.1.1/cmdtools')
    for name,dest in [('network_binary.nb',nbg_name),
                      ('nbg_meta.json',meta_name)]:
        pattern='wksp_nbg_unify/'+name
        matches=glob.glob(pattern)
        if not matches:
            pattern='wksp*/**/'+name
            matches=glob.glob(pattern,recursive=True)
        assert len(matches)==1, (pattern,matches)
        shutil.copyfile(matches[0],dest)
outputs=[p for p in Path('simulator').rglob('iter_*_output_*out0*.tensor') if p.is_file()]
if len(outputs)==800:
    print('reusing 800 simulator output tensors')
else:
    run('inference','--model','aug.json','--model-data','aug.data',
        '--dtype','quantized','--model-quantize','aug.quantize',
        '--iterations','800','--device','CPU','--output-dir','simulator',
        '--postprocess-file','post.yml','--with-input-meta','input.yml')
