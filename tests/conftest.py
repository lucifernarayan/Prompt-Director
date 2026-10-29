import os
import pytest

# Ensure test suite runs with tiny TIME_SCALE so mock sleeps do not stall tests
os.environ["TIME_SCALE"] = "0.001"
