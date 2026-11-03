import json

import monitor_health as mh


def test_failure_alert_is_sent_once_per_day(tmp_path, monkeypatch):
    monkeypatch.setattr(mh, "STATE", tmp_path / "health.json")
    sent = []
    monkeypatch.setattr(mh, "send_discord", lambda message, **kwargs: sent.append(message) or True)
    assert mh.report_failure("뉴스 후보", "뉴스 API 사용량 제한")
    assert mh.report_failure("뉴스 후보", "뉴스 API 사용량 제한")
    assert len(sent) == 1
    assert "새 뉴스·공시를 확인하지 못함" in sent[0]
    assert "뉴스 후보" in json.loads(mh.STATE.read_text())


def test_failed_delivery_is_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(mh, "STATE", tmp_path / "health.json")
    monkeypatch.setattr(mh, "send_discord", lambda *args, **kwargs: False)
    assert not mh.report_failure("보유 공시")
    assert not mh.STATE.exists()
