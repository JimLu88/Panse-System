"""National Day title recognition does not expand campaign scope."""
from app.services import campaign_official_discovery as discovery


def test_exact_observed_national_title_uses_fixed_read_only_detail(monkeypatch):
    from app.services import web_agent_service
    calls=[]
    monkeypatch.setattr(web_agent_service,'ensure_online',
                        lambda db,**kw:calls.append(('wake',kw)) or {'online':True})
    monkeypatch.setattr(web_agent_service,'_post_raw',
                        lambda db,path,body,**kw:calls.append(('observe',path,body)) or {'ok':True})
    monkeypatch.setattr(discovery,'detail',lambda result,title:{'ok':True,'title':title})
    assert discovery.observe(None,title='2026年国庆狂欢-淘宝')=={
        'ok':True,'title':'2026年国庆狂欢-淘宝'}
    assert calls[1][2]['title']=='2026年国庆狂欢-淘宝'
    assert calls[1][2]['stage']=='detail'


def test_unobserved_or_ambiguous_title_never_wakes(monkeypatch):
    from app.services import web_agent_service
    monkeypatch.setattr(web_agent_service,'ensure_online',
                        lambda *a,**kw:(_ for _ in ()).throw(AssertionError('must not wake')))
    for title in ('2026年国庆狂欢-淘宝其他','国庆狂欢-淘宝','2026年国庆狂欢-淘宝\n其他'):
        result=discovery.observe(None,title=title)
        assert result['error']=='exact_visible_campaign_title_required'
