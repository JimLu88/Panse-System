import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign_edge_client import EdgeClient

def test_loopback_no_environment_proxy():
    client=EdgeClient('test-only-token')
    try:assert client.session.trust_env is False
    finally:client.session.close()

def test_injected_transport_preserved():
    class Mock:
        trust_env=True
    session=Mock()
    assert EdgeClient('test-only-token',session=session).session is session
    assert session.trust_env is True
