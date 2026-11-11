from stage1 import working_scale


def test_working_scale():
    assert working_scale(1315) == 1.0 and working_scale(1800) == 1.0   # sample scans: unchanged
    assert abs(working_scale(6534) - 6534 / 1315) < 1e-9               # 600 DPI scan: downscaled copy
