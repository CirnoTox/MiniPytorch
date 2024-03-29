"""The module.
"""
from typing import List, Callable, Any
from mpt.autograd import Tensor
from mpt import ops
import mpt.utility as init
import numpy as np

import functools


class Parameter(Tensor):
    """A special kind of tensor that represents parameters."""


def _unpack_params(value: object) -> List[Tensor]:
    if isinstance(value, Parameter):
        return [value]
    elif isinstance(value, Module):
        return value.parameters()
    elif isinstance(value, dict):
        params = []
        for k, v in value.items():
            params += _unpack_params(v)
        return params
    elif isinstance(value, (list, tuple)):
        params = []
        for v in value:
            params += _unpack_params(v)
        return params
    else:
        return []


def _child_modules(value: object) -> List["Module"]:
    if isinstance(value, Module):
        modules = [value]
        modules.extend(_child_modules(value.__dict__))
        return modules
    if isinstance(value, dict):
        modules = []
        for k, v in value.items():
            modules += _child_modules(v)
        return modules
    elif isinstance(value, (list, tuple)):
        modules = []
        for v in value:
            modules += _child_modules(v)
        return modules
    else:
        return []


class Module:
    def __init__(self):
        self.training = True

    def parameters(self) -> List[Tensor]:
        """Return the list of parameters in the module."""
        return _unpack_params(self.__dict__)

    def _children(self) -> List["Module"]:
        return _child_modules(self.__dict__)

    def eval(self):
        self.training = False
        for m in self._children():
            m.training = False

    def train(self):
        self.training = True
        for m in self._children():
            m.training = True

    def __call__(self, *args, **kwargs):
        return self.forward(*args, **kwargs)


class Identity(Module):
    def forward(self, x):
        return x


class Linear(Module):
    def __init__(self, in_features, out_features, bias=True, device=None, dtype="float32"):
        super().__init__()
        self.in_features = in_features
        self.out_features = out_features

        self.weight = Parameter(
            init.kaiming_uniform(in_features, out_features))
        self.bias = Parameter(init.kaiming_uniform(
            out_features, 1).transpose() if bias is True else None)

    def forward(self, X: Tensor) -> Tensor:
        if self.bias is not None:
            return X@self.weight+self.bias.broadcast_to((X.shape[0], self.out_features))
        else:
            return X@self.weight



class Flatten(Module):
    def forward(self, X):
        flattenSize = functools.reduce(lambda a, b: a*b, X.shape[1:])
        return ops.reshape(X, (X.shape[0], flattenSize))


class ReLU(Module):
    def forward(self, x: Tensor) -> Tensor:
        return ops.relu(x)


class Sequential(Module):
    def __init__(self, *modules):
        super().__init__()
        self.modules = modules

    def forward(self, x: Tensor) -> Tensor:
        for m in self.modules:
            x = m.forward(x)
        return x


class SoftmaxLoss(Module):
    def forward(self, logits: Tensor, y: Tensor):
        oneHot = init.one_hot(logits.shape[1], y)
        logSumExp = ops.logsumexp(logits, axes=1)
        sumMul = ops.summation(ops.multiply(logits, oneHot), axes=(1,))
        neg=-sumMul
        add=logSumExp+neg
        summa=ops.summation(
            add, axes=(0,)
        )
        return  summa/ Tensor([oneHot.shape[0]])


class BatchNorm1d(Module):
    # https://github.com/bettersemut/dlsys_hw2/blob/8b16e4ecac6cf5d5efb2c4840f9107cdfe64e00b/python/needle/nn.py#L148
    def __init__(self, dim, eps=1e-5, momentum=0.1, device=None, dtype="float32"):
        super().__init__()
        self.dim = dim
        self.eps = eps
        self.momentum = momentum
        self.weight = Parameter(init.ones(self.dim, requires_grad=True))
        self.bias = Parameter(init.zeros(self.dim, requires_grad=True))
        self.running_mean = init.zeros(self.dim, requires_grad=False)
        self.running_var = init.ones(self.dim, requires_grad=False)

    def forward(self, x: Tensor) -> Tensor:
        mu_raw = x.sum(axes=0) / x.shape[0]
        mu = ops.broadcast_to(mu_raw.reshape((1, -1)), x.shape)
        x_fixed = x - mu
        var_raw = (x_fixed * x_fixed / x.shape[0]).sum(axes=0)
        var = ops.broadcast_to(var_raw.reshape((1, -1)), x.shape)
        w = ops.broadcast_to(self.weight.reshape((1, -1)), x.shape)
        b = ops.broadcast_to(self.bias.reshape((1, -1)), x.shape)
        if self.training:
            self.running_mean = (1 - self.momentum) * \
                self.running_mean.detach() + self.momentum * mu_raw
            self.running_var = (1 - self.momentum) * \
                self.running_var.detach() + self.momentum * var_raw
            return w * (x - mu) / (var + self.eps) ** 0.5 + b
        else:
            return w * (x - self.running_mean.detach().reshape((1, -1)).broadcast_to(x.shape)) / (self.running_var.detach().reshape((1, -1)).broadcast_to(x.shape) + self.eps) ** 0.5 + b


class LayerNorm1d(Module):
    def __init__(self, dim, eps=1e-5, device=None, dtype="float32"):
        super().__init__()
        self.dim = dim
        self.eps = eps
        self.weight = Parameter(init.ones(dim, requires_grad=True).reshape((1, -1)))
        self.bias = Parameter(init.zeros(dim, requires_grad=True).reshape((1, -1)))

    def forward(self, x: Tensor) -> Tensor:
        E = ops.broadcast_to(ops.reshape(ops.summation(
            x, axes=1)/x.shape[1], (-1, 1)), x.shape)
        var = ops.broadcast_to(ops.reshape(ops.summation(
            (x-E)**2, axes=1)/x.shape[1], (-1, 1)), x.shape)
        x_fin = (x+(-E))/(var+self.eps)**0.5
        w = ops.broadcast_to(self.weight, x.shape)
        b = ops.broadcast_to(self.bias, x.shape)
        return w*x_fin + b


class Dropout(Module):
    def __init__(self, p=0.5):
        super().__init__()
        self.p = p

    def forward(self, x: Tensor) -> Tensor:
        # https://github.com/bettersemut/dlsys_hw2/blob/8b16e4ecac6cf5d5efb2c4840f9107cdfe64e00b/python/needle/nn.py#L225
        if self.training:
            mask = init.randb(*x.shape, p=1 - self.p, dtype=x.dtype, requires_grad=True)
            return x * mask / (1 - self.p)
        else:
            return x


class Residual(Module):
    def __init__(self, fn: Module):
        super().__init__()
        self.fn = fn

    def forward(self, x: Tensor) -> Tensor:
        fn = self.fn
        return fn(x)+x
