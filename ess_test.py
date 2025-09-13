import math
from pyESS import get_stick_output, ESS_IN, ESS_OUT, SMOOTHING

def reference_ess(x, y, prev_x, prev_y):
    magnitude = math.hypot(x, y)
    if magnitude == 0.0:
        return 0.0, 0.0
    x = x / magnitude
    y = y / magnitude
    if magnitude <= ESS_IN:
        out_magnitude = 0.0
    elif magnitude >= ESS_OUT:
        out_magnitude = 1.0
    else:
        out_magnitude = (magnitude - ESS_IN) / (ESS_OUT - ESS_IN)
    if ESS_IN < magnitude < ESS_OUT:
        theta = math.atan2(y, x)
        step = 2 * math.pi / 16
        snapped_theta = round(theta / step) * step
        ess_x = math.cos(snapped_theta)
        ess_y = math.sin(snapped_theta)
        out_x = SMOOTHING * (ess_x * out_magnitude) + (1 - SMOOTHING) * prev_x
        out_y = SMOOTHING * (ess_y * out_magnitude) + (1 - SMOOTHING) * prev_y
    else:
        out_x = SMOOTHING * (x * out_magnitude) + (1 - SMOOTHING) * prev_x
        out_y = SMOOTHING * (y * out_magnitude) + (1 - SMOOTHING) * prev_y
    return max(-1, min(1, out_x)), max(-1, min(1, out_y))

def run_tests():
    test_values = [
        (0.0, 0.0),
        (0.1, 0.0),
        (0.15, 0.0),
        (0.3, 0.3),
        (0.5, 0.5),
        (0.65, 0.0),
        (0.8, 0.0),
        (1.0, 0.0),
        (0.4, 0.9),
        (-0.4, -0.9),
    ]
    prev_x, prev_y = 0.0, 0.0
    print(f"{'Input':>12} | {'Your ESS':>20} | {'Ref ESS':>20}")
    print("-"*60)
    for x, y in test_values:
        out_x, out_y = get_stick_output(x, y, prev_x, prev_y)
        ref_x, ref_y = reference_ess(x, y, prev_x, prev_y)
        print(f"({x:5.2f},{y:5.2f}) | ({out_x:7.4f},{out_y:7.4f}) | ({ref_x:7.4f},{ref_y:7.4f})")
        prev_x, prev_y = out_x, out_y

if __name__ == "__main__":
    run_tests()