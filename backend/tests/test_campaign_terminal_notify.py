from app.api import web_agent
from app.services import notify_service


def test_campaign_requires_feishu_and_reports_delivery(monkeypatch):
    calls=[]
    monkeypatch.setattr(web_agent,'_agent_notice_channel',lambda db:'feishu')
    monkeypatch.setattr(notify_service,'notify',lambda *args,**kw:calls.append(kw) or (True,'sent'))
    assert web_agent.campaign_notify_capabilities(None)['ready'] is True
    result=web_agent.agent_notify(web_agent.AgentNotify(kind='campaign_terminal',text='本轮结果'),None)
    assert result['ok'] and result['delivered'] and result['channel']=='feishu'
    assert calls[0]['wechat_allowed'] is False and calls[0]['enqueue_on_failure'] is False
    monkeypatch.setattr(web_agent,'_agent_notice_channel',lambda db:'wechat')
    assert web_agent.campaign_notify_capabilities(None)['ready'] is False
    assert web_agent.agent_notify(web_agent.AgentNotify(kind='campaign_terminal',text='结果'),None)['delivered'] is False
    assert len(calls)==1


def test_campaign_failed_delivery_not_reported_as_success(monkeypatch):
    monkeypatch.setattr(web_agent,'_agent_notice_channel',lambda db:'feishu')
    monkeypatch.setattr(notify_service,'notify',lambda *a,**k:(False,'timeout'))
    result=web_agent.agent_notify(web_agent.AgentNotify(kind='campaign_terminal',text='结果'),None)
    assert result['ok'] is False and result['delivered'] is False
