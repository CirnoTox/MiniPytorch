import sys
sys.path.append('./python')
import numpy as np
import pytest
import mugrade
import mpt
from mpt import backend_ndarray as nd

print(mpt.Tensor([1,2,3])*mpt.Tensor([1,2,3]))