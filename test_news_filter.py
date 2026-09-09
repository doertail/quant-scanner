"""test_news_filter.py — 로컬 LLM 감성 필터 (Ollama 호출은 전부 모킹)."""

import json

import news_filter


class _Resp:
    def __init__(self, content: str):
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {'message': {'content': self._content}}


def _news(title: str) -> list[dict]:
    return [{'title': title, 'providerPublishTime': 9_999_999_999}]


def test_payload_and_parse(monkeypatch):
    sent = {}

    def fake_post(url, json=None, timeout=None):
        sent['url'], sent['body'], sent['timeout'] = url, json, timeout
        return _Resp('{"verdict":"SKIP","score":0.05,"summary":"파산 신청"}')

    monkeypatch.setattr(news_filter.requests, 'post', fake_post)
    out = news_filter.analyze_news_sentiment('XYZ', _news('XYZ files for bankruptcy'), 'qwen3.5:9b')

    assert out == {'verdict': 'SKIP', 'score': 0.05, 'summary': '파산 신청'}
    assert sent['url'].endswith('/api/chat')
    assert sent['body']['model'] == 'qwen3.5:9b'
    assert sent['body']['think'] is False          # 켜지면 지연 3배 + JSON 깨짐
    assert sent['body']['stream'] is False
    assert sent['body']['format'] == news_filter._VERDICT_SCHEMA
    assert 'XYZ files for bankruptcy' in sent['body']['messages'][0]['content']


def test_server_down_falls_back_to_pass(monkeypatch):
    def boom(*a, **kw):
        raise ConnectionError('ollama down')

    monkeypatch.setattr(news_filter.requests, 'post', boom)
    assert news_filter.analyze_news_sentiment('XYZ', _news('anything'), 'm') == news_filter._SENTIMENT_PASS


def test_garbage_response_falls_back_to_pass(monkeypatch):
    monkeypatch.setattr(news_filter.requests, 'post', lambda *a, **kw: _Resp('사고중... 음'))
    assert news_filter.analyze_news_sentiment('XYZ', _news('anything'), 'm') == news_filter._SENTIMENT_PASS


def test_bad_verdict_value_becomes_pass(monkeypatch):
    monkeypatch.setattr(news_filter.requests, 'post',
                        lambda *a, **kw: _Resp(json.dumps({'verdict': 'SELL', 'score': 9, 'summary': 'x'})))
    out = news_filter.analyze_news_sentiment('XYZ', _news('anything'), 'm')
    assert out['verdict'] == 'PASS'
    assert out['score'] == 1.0        # 범위 밖은 클램프


def test_skip_removes_candidate(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        verdict = 'SKIP' if 'BAD' in json['messages'][0]['content'] else 'PASS'
        return _Resp('{"verdict":"%s","score":0.5,"summary":"s"}' % verdict)

    monkeypatch.setattr(news_filter, 'get_ticker_news', lambda t, **kw: _news(f'{t} headline'))
    monkeypatch.setattr(news_filter.requests, 'post', fake_post)

    cands = [{'ticker': 'BAD'}, {'ticker': 'OK'}]
    kept, skipped = news_filter.filter_candidates_by_sentiment(cands, 'm', top_n=5)

    assert [c['ticker'] for c in kept] == ['OK']
    assert [c['ticker'] for c in skipped] == ['BAD']
    assert cands[0]['sentiment']['verdict'] == 'SKIP'


def test_beyond_top_n_gets_default_pass(monkeypatch):
    monkeypatch.setattr(news_filter, 'get_ticker_news', lambda t, **kw: _news('h'))
    monkeypatch.setattr(news_filter.requests, 'post',
                        lambda *a, **kw: _Resp('{"verdict":"PASS","score":0.9,"summary":"good"}'))

    cands = [{'ticker': 'A'}, {'ticker': 'B'}]
    kept, _ = news_filter.filter_candidates_by_sentiment(cands, 'm', top_n=1)

    assert kept[0]['sentiment']['score'] == 0.9
    assert kept[1]['sentiment'] == news_filter._SENTIMENT_PASS
