"""Materialize exact dequantized repeat-3 INT8 inputs for Acuity (new work dir)."""
import argparse
import json
import numpy as np
from common import HERE, MODEL, baseline, interpreter, digest, predict, WORK, CONVERSION_NAMES
p=argparse.ArgumentParser()
p.add_argument('--validate-only',action='store_true')
a=p.parse_args()
d, ids=baseline()
interpreter()
print(json.dumps(dict(model_sha256=digest(MODEL),windows=len(ids),
                      input_scale=1/255,output_scale=1/256,zero_point=-128)))
if not a.validate_only:
    out=WORK
    out.mkdir()
    (out/'inputs').mkdir()
    for j, x in enumerate(d['input_int8'][ids]):
        np.savetxt(out/f'inputs/{j:04d}.tensor',((x.astype(float)+128)/255).reshape(48,4),fmt='%.10g')
    (out/'dataset.txt').write_text(''.join(f'/work/inputs/{j:04d}.tensor\n' for j in range(800)))
    template=HERE.parent/'toolkit/ameba-ai-offline-toolkit-main/Docker/Linux/acuity_examples_c901149/Models/vlc_edge_cnn_ch4'
    (out/'input.yml').write_bytes((template/'vlc_edge_cnn_ch4_inputmeta.yml').read_bytes())
    (out/'post.yml').write_bytes((template/'vlc_edge_cnn_ch4_postprocess_file.yml').read_bytes())
    np.save(out/'host_int8.npy',predict(d['input_int8'][ids]))
    (out/'manifest.json').write_text(json.dumps(dict(model_sha256=digest(MODEL),
        **CONVERSION_NAMES, input_sha256=__import__('hashlib').sha256(d['input_int8'][ids].tobytes()).hexdigest(),
        sessions=d['session'][ids].tolist(),frame_ids=d['frame_index'][ids].tolist()),indent=2))
