"""Train an Ameba-oriented VLC decoder using conversion-friendly CNN ops."""

import argparse
import json
import os

import numpy as np
import tensorflow as tf
from tensorflow.keras.callbacks import EarlyStopping, ModelCheckpoint, ReduceLROnPlateau
from tensorflow.keras.layers import BatchNormalization, Conv2D, Input, Reshape
from tensorflow.keras.models import Model
from tensorflow.keras.optimizers import Adam

from train_model_advanced import (
    augment_data,
    bit_sample_weights,
    decoder_metrics,
    set_seed,
    split_dataset,
)


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def build_edge_model(input_shape, learning_rate):
    """NPU-native CNN with the four per-bit features stored as channels."""
    inputs = Input(shape=input_shape, name="vlc_features")
    # Input is (48 bits, 1 column, 4 channels). A 1x1 convolution mixes the
    # four features without the layout-sensitive (1x4)+Squeeze path generated
    # by Acuity for the old (48, 4, 1) representation.
    x = Conv2D(24, (1, 1), padding="valid", activation="relu", name="bit_features")(inputs)
    x = BatchNormalization(name="bit_bn")(x)
    x = Conv2D(48, (5, 1), padding="same", activation="relu", name="context_1")(x)
    x = BatchNormalization(name="context_bn_1")(x)
    x = Conv2D(48, (5, 1), padding="same", activation="relu", name="context_2")(x)
    x = BatchNormalization(name="context_bn_2")(x)
    x = Conv2D(24, (3, 1), padding="same", activation="relu", name="context_3")(x)
    logits = Conv2D(1, (1, 1), activation="sigmoid", name="bit_probability_map")(x)
    outputs = Reshape((48, 1), name="bit_probabilities")(logits)
    model = Model(inputs, outputs, name="vlc_edge_cnn")
    model.compile(
        optimizer=Adam(learning_rate=learning_rate),
        loss="binary_crossentropy",
        metrics=["binary_accuracy", tf.keras.metrics.AUC(name="auc")],
    )
    return model


def representative_dataset(X):
    for sample in X[: min(len(X), 200)]:
        yield [sample[np.newaxis].astype("float32")]


def export_int8(model, representative_X, output_path):
    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.representative_dataset = lambda: representative_dataset(representative_X)
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type = tf.int8
    converter.inference_output_type = tf.int8
    payload = converter.convert()
    with open(output_path, "wb") as stream:
        stream.write(payload)
    return len(payload)


def main():
    parser = argparse.ArgumentParser(description="Train and INT8-export the edge VLC CNN")
    parser.add_argument("--dataset_dir", default="dataset")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--batch_size", type=int, default=16)
    parser.add_argument("--learning_rate", type=float, default=5e-4)
    parser.add_argument("--val_size", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no_augment", action="store_true")
    args = parser.parse_args()
    set_seed(args.seed)

    X_train_raw, X_val, y_train_raw, y_val, _ = split_dataset(args)
    if X_train_raw is None or X_val is None:
        raise SystemExit(1)
    # Collector artifacts are stored as (N, 48, 4, 1). Move the four features
    # to NHWC channels so VIP8000 does not need a spatial squeeze operation.
    X_train_raw = np.transpose(X_train_raw, (0, 1, 3, 2))
    X_val = np.transpose(X_val, (0, 1, 3, 2))

    X_train, y_train = (
        (X_train_raw, y_train_raw)
        if args.no_augment else augment_data(X_train_raw, y_train_raw)
    )

    model = build_edge_model(X_train_raw.shape[1:], args.learning_rate)
    keras_path = os.path.join(SCRIPT_DIR, "vlc_edge_cnn_model.keras")
    callbacks = [
        EarlyStopping(monitor="val_auc", mode="max", patience=15, restore_best_weights=True),
        ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=6, min_lr=1e-5),
        ModelCheckpoint(keras_path, monitor="val_auc", mode="max", save_best_only=True),
    ]
    model.fit(
        X_train,
        y_train,
        sample_weight=bit_sample_weights(y_train),
        validation_data=(X_val, y_val, bit_sample_weights(y_val)),
        epochs=args.epochs,
        batch_size=args.batch_size,
        callbacks=callbacks,
        verbose=2,
    )
    model = tf.keras.models.load_model(keras_path)
    metrics = decoder_metrics(model, X_val, y_val, args.batch_size)
    metrics.update({
        "parameters": int(model.count_params()),
        "npu_input_layout": "NHWC_1x48x1x4",
        "architecture_revision": "channel_features_v2",
    })

    tflite_path = os.path.join(SCRIPT_DIR, "vlc_edge_cnn_int8.tflite")
    metrics["tflite_int8_bytes"] = export_int8(model, X_train_raw, tflite_path)
    metrics_path = os.path.join(SCRIPT_DIR, "validation_metrics_EDGE.json")
    with open(metrics_path, "w", encoding="utf-8") as stream:
        json.dump(metrics, stream, indent=2)

    print("\n[EDGE RESULT]")
    for key, value in metrics.items():
        print(f"  - {key}: {value}")
    print(f"[SUCCESS] Keras: {keras_path}")
    print(f"[SUCCESS] INT8  : {tflite_path}")
    print(f"[SUCCESS] Report: {metrics_path}")


if __name__ == "__main__":
    main()
