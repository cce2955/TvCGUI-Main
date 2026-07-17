from pathlib import Path


def test_frame_data_window_keeps_timing_and_cancel_launchers():
    text = Path('tvcgui/features/frame_data/window.py').read_text(encoding='utf-8')
    assert 'apply_observations_to_scan_data' in text
    for name in (
        'open_frame_data_window',
        'open_cancel_mapper_window',
        'open_cancel_mapper_loading_window',
        'close_cancel_mapper_loading_window',
        'open_cancel_lab_window',
        'open_cancel_lab_loading_window',
        'close_cancel_lab_loading_window',
    ):
        assert f'def {name}(' in text
