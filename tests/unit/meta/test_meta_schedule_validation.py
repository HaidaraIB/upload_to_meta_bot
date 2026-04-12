"""Rules for Meta-native (Graph scheduled_publish_time) schedule windows."""

from datetime import datetime, timedelta, timezone

import pytest

from meta.errors import MetaPublishUserError
from meta.publishers import _validate_meta_native_schedule_window


def test_meta_schedule_window_skips_when_not_meta_backend():
    payload = {
        "schedule_mode": "schedule",
        "schedule_backend": "bot",
        "scheduled_utc_dt": datetime.now(timezone.utc) + timedelta(minutes=1),
    }
    _validate_meta_native_schedule_window(payload)


def test_meta_schedule_window_too_soon_raises():
    now = datetime.now(timezone.utc)
    payload = {
        "schedule_mode": "schedule",
        "schedule_backend": "meta",
        "scheduled_utc_dt": now + timedelta(minutes=5),
    }
    with pytest.raises(MetaPublishUserError) as exc:
        _validate_meta_native_schedule_window(payload)
    assert exc.value.message_key == "meta_err_meta_schedule_min_lead"


def test_meta_schedule_window_beyond_horizon_raises():
    now = datetime.now(timezone.utc)
    payload = {
        "schedule_mode": "schedule",
        "schedule_backend": "meta",
        "scheduled_utc_dt": now + timedelta(days=45),
    }
    with pytest.raises(MetaPublishUserError) as exc:
        _validate_meta_native_schedule_window(payload)
    assert exc.value.message_key == "meta_err_meta_schedule_max_horizon"


def test_meta_schedule_window_accepts_mid_range():
    now = datetime.now(timezone.utc)
    payload = {
        "schedule_mode": "schedule",
        "schedule_backend": "meta",
        "scheduled_utc_dt": now + timedelta(days=2, hours=5),
    }
    _validate_meta_native_schedule_window(payload)
