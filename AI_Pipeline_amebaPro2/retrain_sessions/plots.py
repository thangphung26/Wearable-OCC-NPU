#!/usr/bin/env python3
"""Publication plots from completed runs only; never reads or writes LOSO."""
from session_config import RESULTS
import argparse
import csv
import json
import os
from pathlib import Path

os.environ.setdefault('MPLCONFIGDIR', '/tmp/robust_c_mpl')
os.environ.setdefault('XDG_CACHE_HOME', '/tmp/robust_c_cache')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import beta

HERE = Path(__file__).resolve().parent
# RESULTS is selected by session_config.
NAMES = ['classic', 'old_int8', 'new_int8']
LABELS = ['Classic', 'Old INT8', 'New INT8']


def ci(errors, n):
    return [0. if errors == 0 else float(beta.ppf(.025, errors, n-errors+1)),
            1. if errors == n else float(beta.ppf(.975, errors+1, n-errors))]


def errorpoint(ax, x, errors, n, color, marker='o', label=None):
    low, high = ci(errors, n)
    y = errors/n if errors else high
    ax.errorbar(x, y, yerr=[[y-low], [high-y]], color=color, marker=marker,
                markerfacecolor=color if errors else 'none', markersize=3,
                capsize=2, linewidth=.8, label=label)


def save(fig, stem):
    fig.tight_layout(pad=.5)
    fig.savefig(stem.with_suffix('.png'), dpi=300)
    fig.savefig(stem.with_suffix('.pdf'))
    plt.close(fig)


def render(split):
    directory = RESULTS / split
    histories = json.loads((directory / 'history_all.json').read_text())
    best_epochs = []
    fig, ax = plt.subplots(figsize=(3.5, 2.7))
    for i, item in enumerate(histories):
        h = item['history']
        best = int(np.argmin(h['val_loss'])) + 1
        best_epochs.append({'fold': item['fold'], 'seed': item['seed'],
                            'best_epoch': best, 'best_val_loss': h['val_loss'][best-1]})
        color = f'C{i}'
        label = f"{item['fold']} / {item['seed']}"
        epochs = np.arange(1, len(h['loss'])+1)
        ax.plot(epochs, h['loss'], color=color, linewidth=.8, label=label+' train')
        ax.plot(epochs, h['val_loss'], color=color, linestyle='--', linewidth=.8, label=label+' val')
        ax.plot(best, h['val_loss'][best-1], '*', color=color, markersize=6)
    ax.set(xlabel='Epoch (count)', ylabel='Binary cross-entropy (dimensionless)', yscale='log')
    ax.legend(fontsize=5, ncol=2)
    save(fig, RESULTS / f'training_curves_{split}')
    summary_path = directory / 'summary.json'
    summary = json.loads(summary_path.read_text())
    summary['best_epochs'] = best_epochs
    summary['best_epoch_convention'] = '1-based argmin(val_loss); star marks restored best epoch'
    summary_path.write_text(json.dumps(summary, indent=2)+'\n')

    with (directory / 'per_session.csv').open() as stream:
        rows = list(csv.DictReader(stream))
    conditions = sorted({r['condition'] for r in rows}, key=lambda c: (int(c.split('m_')[0]), c))
    fig, ax = plt.subplots(figsize=(3.5, 2.7))
    for j, (name, label) in enumerate(zip(NAMES, LABELS)):
        for i, condition in enumerate(conditions):
            sub = [r for r in rows if r['condition'] == condition]
            errors = sum(int(r[name+'_errors']) for r in sub)
            n = sum(int(r['n_bits']) for r in sub)
            errorpoint(ax, i+(j-1)*.22, errors, n, f'C{j}', label=label if i == 0 else None)
    ax.set_xticks(range(len(conditions)), [c.split('m_')[0]+' m '+c.split('m_')[1].upper() for c in conditions], rotation=45, ha='right', fontsize=6)
    ax.set(ylabel='Bit error rate (dimensionless)', yscale='log')
    ax.legend(fontsize=6)
    fig.text(.5, .01, '95% CP intervals; open markers: zero errors at upper bound', ha='center', fontsize=5)
    fig.subplots_adjust(bottom=.34, left=.19, right=.98, top=.98)
    fig.savefig(RESULTS / f'ber_by_condition_{split}.png', dpi=300)
    fig.savefig(RESULTS / f'ber_by_condition_{split}.pdf')
    plt.close(fig)
    print(json.dumps({'split': split, 'best_epochs': best_epochs}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('splits', nargs='*', choices=['repeat', 'lodo'], default=['repeat', 'lodo'])
    args = parser.parse_args()
    plt.rcParams.update({'font.size': 7, 'axes.linewidth': .6, 'pdf.fonttype': 42})
    for split in args.splits:
        render(split)


if __name__ == '__main__':
    main()
