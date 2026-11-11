import argparse
import glob
import json
import os
import random

import matplotlib.pyplot as plt
import numpy as np
import tensorflow as tf
from tensorflow.keras.callbacks import EarlyStopping, ModelCheckpoint, ReduceLROnPlateau
from tensorflow.keras.layers import (
    BatchNormalization, Bidirectional, Conv1D, Dense, Dropout, GRU,
    Input, LayerNormalization, MaxPooling1D, MultiHeadAttention, TimeDistributed, Add, GlobalAveragePooling1D
)
from tensorflow.keras.models import Model
from tensorflow.keras.optimizers import Adam


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    tf.random.set_seed(seed)


def dataset_path(path):
    if os.path.isabs(path):
        return path
    # Commands are normally launched from the project root, where dataset/
    # lives beside AI_Pipeline_amebaPro2 rather than inside it.
    cwd_path = os.path.abspath(path)
    return cwd_path if os.path.exists(cwd_path) else os.path.join(SCRIPT_DIR, path)


def paired_npy_files(dataset_dir, x_suffix="_Xseq.npy", y_suffix="_yseq.npy"):
    x_files = sorted(glob.glob(os.path.join(dataset_dir, f"*{x_suffix}")))
    pairs = []

    for x_file in x_files:
        y_file = x_file[: -len(x_suffix)] + y_suffix
        if not os.path.exists(y_file):
            print(f"[WARNING] Missing sequence label file for {os.path.basename(x_file)}")
            continue
        pairs.append((x_file, y_file))

    return pairs


def load_dataset(dataset_dir="dataset"):
    dataset_dir = dataset_path(dataset_dir)
    print(f"[INFO] Scanning sequence dataset: {dataset_dir}")
    pairs = paired_npy_files(dataset_dir)

    if not pairs:
        print("[ERROR] No *_Xseq.npy / *_yseq.npy pairs found. Run auto_labeler.py first.")
        return None, None, None

    X_list = []
    y_list = []
    groups_list = []
    expected_sample_shape = None

    for file_idx, (x_file, y_file) in enumerate(pairs):
        X_data = np.load(x_file).astype("float32")
        y_data = np.load(y_file).astype("float32")

        if X_data.ndim == 3:
            X_data = np.expand_dims(X_data, axis=-1)
        if y_data.ndim == 2:
            y_data = np.expand_dims(y_data, axis=-1)

        if X_data.ndim != 4 or X_data.shape[1] != 48:
            print(f"[WARNING] Skip bad Xseq shape {X_data.shape}: {os.path.basename(x_file)}")
            continue
        if y_data.shape != (X_data.shape[0], X_data.shape[1], 1):
            print(f"[WARNING] Skip mismatched Xseq/yseq {X_data.shape}/{y_data.shape}: {os.path.basename(x_file)}")
            continue

        sample_shape = X_data.shape[1:]
        if expected_sample_shape is None:
            expected_sample_shape = sample_shape
        elif sample_shape != expected_sample_shape:
            raise ValueError(
                "Mixed sequence shapes in one split: "
                f"expected {expected_sample_shape}, got {sample_shape} in "
                f"{os.path.basename(x_file)}. Re-label all sessions with the "
                "same dataset_config.json timing before training."
            )

        X_list.append(X_data)
        y_list.append(y_data)
        groups_list.extend([file_idx] * len(y_data))

    if not X_list:
        print("[ERROR] All sequence dataset pairs were invalid.")
        return None, None, None

    X_all = np.vstack(X_list)
    y_all = np.vstack(y_list)
    groups_all = np.array(groups_list)

    print("\n[INFO] Loaded sequence dataset")
    print(f"  - Sequences: {len(y_all)}")
    print(f"  - Files: {len(X_list)}")
    print(f"  - X shape: {X_all.shape}")
    print(f"  - y shape: {y_all.shape}")
    print(f"  - bit 0 / bit 1: {np.sum(y_all == 0)} / {np.sum(y_all == 1)}")

    return X_all, y_all, groups_all


def augment_data(X, y, noise_std=0.04):
    print("\n[INFO] Augmenting train split (x4: original + noise + drift + clipping)")
    X_aug = [X]
    y_aug = [y]

    noise = np.random.normal(0, noise_std, X.shape).astype("float32")
    X_aug.append(np.clip(X + noise, 0, 1))
    y_aug.append(y)

    t = np.linspace(0, 1, X.shape[1], dtype="float32").reshape(1, -1, 1, 1)
    drift_freq = np.random.uniform(0.5, 2.0, size=(len(X), 1, 1, 1)).astype("float32")
    drift_amp = np.random.uniform(0.04, 0.12, size=(len(X), 1, 1, 1)).astype("float32")
    drift = drift_amp * np.sin(2 * np.pi * drift_freq * t)
    X_aug.append(np.clip(X + drift, 0, 1))
    y_aug.append(y)

    clipped = X.copy()
    clip_len = max(1, X.shape[1] // 4)
    for i in range(len(clipped)):
        start = np.random.randint(0, X.shape[1] - clip_len + 1)
        gain = np.random.uniform(1.25, 1.7)
        clipped[i, start : start + clip_len] = np.clip(clipped[i, start : start + clip_len] * gain, 0, 1)
    X_aug.append(clipped)
    y_aug.append(y)

    X_out = np.concatenate(X_aug, axis=0).astype("float32")
    y_out = np.concatenate(y_aug, axis=0).astype("float32")
    print(f"  - Augmented X shape: {X_out.shape}")
    return X_out, y_out


def build_advanced_model(input_shape, learning_rate):
    """
    Advanced Architecture:
    1. Feature Extraction: CNN 1D
    2. Deep Sequence Learning: 2 stacked layers of Bidirectional GRU
    3. Global Context: Multi-Head Self-Attention
    """
    inputs = Input(shape=input_shape)
    
    # ---- 1. CNN Feature Extractor ----
    x = TimeDistributed(Conv1D(32, kernel_size=3, activation="relu", padding="same"))(inputs)
    x = TimeDistributed(BatchNormalization())(x)
    x = TimeDistributed(MaxPooling1D(pool_size=2))(x)
    
    x = TimeDistributed(Conv1D(64, kernel_size=3, activation="relu", padding="same"))(x)
    x = TimeDistributed(BatchNormalization())(x)
    x = TimeDistributed(GlobalAveragePooling1D())(x)
    
    # ---- 2. Deep Bidirectional GRU ----
    x = Bidirectional(GRU(64, return_sequences=True, dropout=0.2))(x)
    x = Bidirectional(GRU(64, return_sequences=True, dropout=0.2))(x)
    
    # ---- 3. Multi-Head Self-Attention ----
    # Helps the model focus on long-range dependencies (e.g. Manchester rules across the whole 48-bit frame)
    attn_out = MultiHeadAttention(num_heads=4, key_dim=64)(x, x)
    x = Add()([x, attn_out]) # Residual connection
    x = LayerNormalization()(x)
    
    # ---- 4. Classifier ----
    x = Dense(64, activation="relu")(x)
    x = Dropout(0.3)(x)
    outputs = Dense(1, activation="sigmoid")(x)
    
    model = Model(inputs=inputs, outputs=outputs)
    
    model.compile(
        optimizer=Adam(learning_rate=learning_rate),
        loss="binary_crossentropy",
        metrics=["binary_accuracy", tf.keras.metrics.AUC(name="auc")],
    )
    return model


def split_dataset(args):
    train_dir = os.path.join(args.dataset_dir, "train")
    val_dir = os.path.join(args.dataset_dir, "val")

    if os.path.exists(dataset_path(train_dir)) and os.path.exists(dataset_path(val_dir)):
        print("\n[INFO] Using fixed dataset/train and dataset/val split")
        X_train, y_train, _ = load_dataset(train_dir)
        X_val, y_val, _ = load_dataset(val_dir)
        if X_train is not None and X_val is not None and X_train.shape[1:] != X_val.shape[1:]:
            raise ValueError(
                f"Train/val input shapes differ: {X_train.shape[1:]} vs "
                f"{X_val.shape[1:]}. Re-label both splits with one timing config."
            )
        return X_train, X_val, y_train, y_val, True

    print("\n[INFO] No fixed split found. Using leakage-safe source-file groups")
    X, y, groups = load_dataset(args.dataset_dir)
    if X is None:
        return None, None, None, None, False

    if len(np.unique(groups)) < 2:
        print("[ERROR] Need at least 2 source files for automatic group split.")
        return None, None, None, None, False

    train_idx, val_idx = group_train_val_indices(
        groups, val_size=args.val_size, seed=args.seed,
    )
    return X[train_idx], X[val_idx], y[train_idx], y[val_idx], False


def bit_sample_weights(y):
    bit_ones = np.sum(y == 1)
    bit_zeros = np.sum(y == 0)
    total = bit_ones + bit_zeros
    zero_weight = total / (2.0 * max(bit_zeros, 1))
    one_weight = total / (2.0 * max(bit_ones, 1))
    return np.where(y[..., 0] == 1, one_weight, zero_weight).astype("float32")


def group_train_val_indices(groups, val_size=0.2, seed=42):
    """Split whole source sessions; never leak one recording across sets."""
    unique_groups = np.unique(groups)
    if len(unique_groups) < 2:
        raise ValueError("Need at least two independent source sessions")
    rng = np.random.default_rng(seed)
    shuffled = rng.permutation(unique_groups)
    val_group_count = min(
        len(unique_groups) - 1,
        max(1, int(round(len(unique_groups) * val_size))),
    )
    val_groups = set(shuffled[:val_group_count].tolist())
    val_mask = np.fromiter((group in val_groups for group in groups), dtype=bool)
    return np.flatnonzero(~val_mask), np.flatnonzero(val_mask)


def plot_history(history, output_path, title):
    plt.figure(figsize=(11, 5))
    plt.suptitle(title, fontsize=14, fontweight="bold")

    metric_name = "binary_accuracy" if "binary_accuracy" in history.history else "accuracy"
    val_metric_name = f"val_{metric_name}"

    plt.subplot(1, 2, 1)
    plt.plot(history.history[metric_name], label="Train Acc")
    plt.plot(history.history[val_metric_name], label="Val Acc")
    if "auc" in history.history:
        plt.plot(history.history["auc"], label="Train AUC")
        plt.plot(history.history["val_auc"], label="Val AUC")
    plt.title("Metrics")
    plt.xlabel("Epoch")
    plt.legend()

    plt.subplot(1, 2, 2)
    plt.plot(history.history["loss"], label="Train Loss")
    plt.plot(history.history["val_loss"], label="Val Loss")
    plt.title("Loss")
    plt.xlabel("Epoch")
    plt.legend()

    plt.tight_layout()
    plt.savefig(output_path, dpi=140)
    plt.close()


def decoder_metrics(model, X, y, batch_size):
    probabilities = model.predict(X, batch_size=batch_size, verbose=0)
    predictions = (probabilities >= 0.5).astype(np.int8)
    targets = y.astype(np.int8)

    bit_error_rate = float(np.mean(predictions != targets))
    exact_frame_rate = float(
        np.mean(np.all(predictions[..., 0] == targets[..., 0], axis=1))
    )
    return {
        "bit_error_rate": bit_error_rate,
        "bit_accuracy": 1.0 - bit_error_rate,
        "exact_48bit_frame_rate": exact_frame_rate,
        "sequences": int(len(y)),
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Train Advanced (Deep Bi-GRU + Attention) sequence bit decoder for 48-bit VLC frames")
    parser.add_argument("--dataset_dir", default="dataset")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--batch_size", type=int, default=32)
    parser.add_argument("--learning_rate", type=float, default=5e-4)
    parser.add_argument("--val_size", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no_augment", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    set_seed(args.seed)

    X_train_raw, X_val, y_train_raw, y_val, fixed_split = split_dataset(args)
    if X_train_raw is None or X_val is None:
        return

    print("\n[INFO] Train/Val summary")
    print(f"  - Train raw: {X_train_raw.shape}, y: {y_train_raw.shape}")
    print(f"  - Val:       {X_val.shape}, y: {y_val.shape}")

    X_train, y_train = (X_train_raw, y_train_raw) if args.no_augment else augment_data(X_train_raw, y_train_raw)

    model = build_advanced_model(input_shape=X_train_raw.shape[1:], learning_rate=args.learning_rate)
    model.summary()

    model_path = os.path.join(SCRIPT_DIR, "vlc_cnn_advanced_model.keras")
    history_path = os.path.join(SCRIPT_DIR, "training_history_ADVANCED.png")
    
    callbacks = [
        EarlyStopping(monitor="val_auc", mode="max", patience=20, restore_best_weights=True),
        ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=8, min_lr=1e-5),
        ModelCheckpoint(model_path, monitor="val_auc", mode="max", save_best_only=True),
    ]

    history = model.fit(
        X_train,
        y_train,
        sample_weight=bit_sample_weights(y_train),
        epochs=args.epochs,
        batch_size=args.batch_size,
        validation_data=(X_val, y_val, bit_sample_weights(y_val)),
        callbacks=callbacks,
        verbose=1,
    )

    # Evaluate the exact checkpoint advertised to the user, even when training
    # reaches the epoch limit without triggering EarlyStopping.
    model = tf.keras.models.load_model(model_path)

    print("\n[INFO] Validation evaluation")
    metrics = model.evaluate(
        X_val,
        y_val,
        sample_weight=bit_sample_weights(y_val),
        verbose=0,
        return_dict=True,
    )
    for name, value in metrics.items():
        print(f"  - {name}: {value:.4f}")

    sequence_metrics = decoder_metrics(model, X_val, y_val, args.batch_size)
    print(f"  - bit_error_rate: {sequence_metrics['bit_error_rate']:.6f}")
    print(
        "  - exact_48bit_frame_rate: "
        f"{sequence_metrics['exact_48bit_frame_rate']:.6f}"
    )

    metrics_path = os.path.join(SCRIPT_DIR, "validation_metrics_ADVANCED.json")
    with open(metrics_path, "w", encoding="utf-8") as metrics_file:
        json.dump(sequence_metrics, metrics_file, indent=4)

    title = "Advanced CNN+DeepBiGRU+Attention Training"
    if fixed_split:
        title += " (Fixed/OOD Split)"
    plot_history(history, history_path, title)
    print(f"\n[SUCCESS] Saved best model: {model_path}")
    print(f"[SUCCESS] Saved history plot: {history_path}")
    print(f"[SUCCESS] Saved validation metrics: {metrics_path}")


if __name__ == "__main__":
    main()
