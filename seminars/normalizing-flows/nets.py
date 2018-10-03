from abc import abstractmethod
from typing import List, Callable, Dict, Any

import numpy as np
import tensorflow as tf
from tqdm import tqdm

import flow_layers as fl

K = tf.keras.backend
keras = tf.keras
import tensorflow.contrib.layers as tf_layers
from tensorflow.python.ops import template as template_ops


def simple_resnet_template_fn(
        name: str,
        activation_fn=tf.nn.relu,
        units_factor: int = 2,
        num_blocks: int = 1
):

    def _shift_and_log_scale_fn(x: tf.Tensor):
        shape = K.int_shape(x)
        num_channels = shape[3]
        num_units = num_channels * units_factor
        h = x
        for u in range(num_blocks):
            with tf.variable_scope(f"ResnetBlock{u}"):
                h_input = h
                # nn definition
                h = tf_layers.conv2d(
                    h_input, num_outputs=num_units, kernel_size=3, activation_fn=activation_fn
                )
                h = tf_layers.conv2d(
                    h,
                    num_outputs=num_units,
                    kernel_size=3,
                    activation_fn=activation_fn,
                )
                h = tf_layers.conv2d(
                    h, num_outputs=num_channels, kernel_size=3, activation_fn=None
                )
                h = activation_fn(h + h_input)

        # create shift and log_scale
        shift = tf_layers.conv2d(
            h,
            num_outputs=num_channels,
            weights_initializer=tf.random_normal_initializer(stddev=0.001),
            kernel_size=3,
            activation_fn=None,
        )
        log_scale = tf_layers.conv2d(
            h,
            num_outputs=num_channels,
            weights_initializer=tf.random_normal_initializer(stddev=0.001),
            kernel_size=3,
            activation_fn=None,
        )
        log_scale = tf.clip_by_value(log_scale, -15.0, 15.0)
        return shift, log_scale

    return template_ops.make_template(name, _shift_and_log_scale_fn)


class TemplateFn:

    def __init__(
            self,
            params: Dict[str, Any],
            template_fn: Callable[[str], Any]
    ):
        self._params = params
        self._template_fn = template_fn

    def create_template_fn(self, name: str):
        return self._template_fn(name=name, **self._params)


class ResentTemplate(TemplateFn):
    def __init__(
            self,
            activation_fn=tf.nn.relu,
            units_factor: int = 2,
            num_blocks: int = 1,
    ) -> None:
        params = {
            "activation_fn": activation_fn,
            "units_factor": units_factor,
            "num_blocks": num_blocks,
        }
        super().__init__(
            params=params,
            template_fn=simple_resnet_template_fn
        )


def step_flow(name: str, shift_and_log_scale_fn):
    actnorm = fl.ActnormLayer()
    layers = [
        actnorm,
        fl.InvertibleConv1x1Layer(),
        fl.AffineCouplingLayer(shift_and_log_scale_fn=shift_and_log_scale_fn),
    ]
    return fl.ChainLayer(layers, name=name), actnorm


def initialize_actnorms(
    sess: tf.Session(),
    feed_dict_fn: Callable[[], Dict[tf.Tensor, np.ndarray]],
    actnorm_layers: List[fl.ActnormLayer],
    num_steps: int = 100,
    num_init_iterations: int = 10,
) -> None:
    for actnorm_layer in tqdm(actnorm_layers):
        init_op = actnorm_layer.get_ddi_init_ops(num_init_iterations)
        for i in range(num_steps):
            sess.run(init_op, feed_dict=feed_dict_fn())


def create_simple_flow(
        num_steps: int = 1,
        num_scales: int = 3,
        template_fn: TemplateFn = ResentTemplate()
) -> (List[fl.FlowLayer], List[fl.ActnormLayer]):

    layers = [fl.LogitifyImage()]
    actnorm_layers = []
    for scale in range(num_scales):
        scale_name = f"Scale{scale+1}"
        scale_steps = []
        for s in range(num_steps):
            name = f"Step{s+1}"
            step_layer, actnorm_layer = step_flow(
                name=name,
                shift_and_log_scale_fn=template_fn.create_template_fn(name)
            )
            scale_steps.append(step_layer)
            actnorm_layers.append(actnorm_layer)

        layers += [
            fl.SqueezingLayer(name=scale_name),
            fl.ChainLayer(scale_steps, name=scale_name),
            fl.FactorOutLayer(name=scale_name),
        ]

    return layers, actnorm_layers
