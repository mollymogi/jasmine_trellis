#!/usr/bin/env python3
"""Install a local bge-reranker ONNX export as the engine's flashrank model.

flashrank only loads models by its own names, so the export is placed under
the multilingual slot (ms-marco-MultiBERT-L-12): XLM-R tokenizer files plus the
ONNX graph renamed to the slot's file name. flashrank feeds input_ids and
attention_mask and applies a sigmoid to a single logit, which matches
bge-reranker. Nothing is downloaded here.

usage: install-reranker.py --base <local-item6> --model-dir <bge dir> --onnx <model.onnx>
"""
import argparse
import shutil
from pathlib import Path

SLOT = 'ms-marco-MultiBERT-L-12'
FILES = ('config.json', 'special_tokens_map.json', 'tokenizer.json', 'tokenizer_config.json')

p = argparse.ArgumentParser()
p.add_argument('--base', required=True)
p.add_argument('--model-dir', required=True)
p.add_argument('--onnx', required=True)
a = p.parse_args()
src = Path(a.model_dir)
dest = Path(a.base) / 'engine' / 'home' / '.cache' / 'flashrank' / SLOT
tmp = dest.with_name(SLOT + '.installing')
shutil.rmtree(tmp, ignore_errors=True)
tmp.mkdir(parents=True)
for name in FILES:
    shutil.copy2(src / name, tmp / name)
shutil.copy2(a.onnx, tmp / 'flashrank-MultiBERT-L12_Q.onnx')
shutil.rmtree(dest, ignore_errors=True)
tmp.rename(dest)
print(dest)
