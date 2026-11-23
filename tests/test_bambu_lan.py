import json
from unittest.mock import MagicMock, patch

import pytest

from bambu_lan import get_bambu_lan_status, BambuLANError


class FakeReasonCode:
    def __init__(self, is_failure=False):
        self.is_failure = is_failure

    def __str__(self):
        return 'Success' if not self.is_failure else 'Not authorized'


def _make_fake_client(report_payload=None, connect_reason_code=None, deliver_report=True):
    """Build a fake paho MQTT client whose connect()/loop_start() synchronously
    drive the real on_connect/on_message callbacks bambu_lan.py registers,
    so we exercise the real request/response logic without a live broker."""
    fake = MagicMock()
    state = {}

    def fake_connect(host, port, keepalive):
        state['host'] = host
        state['port'] = port

    def fake_loop_start():
        # Simulate the broker accepting the connection
        reason_code = connect_reason_code or FakeReasonCode(is_failure=False)
        fake.on_connect(fake, None, MagicMock(), reason_code)
        if deliver_report and report_payload is not None:
            msg = MagicMock()
            msg.payload = json.dumps({'print': report_payload}).encode('utf-8')
            fake.on_message(fake, None, msg)

    fake.connect.side_effect = fake_connect
    fake.loop_start.side_effect = fake_loop_start
    return fake


def test_get_status_returns_parsed_report_on_success():
    report = {'gcode_state': 'RUNNING', 'mc_percent': 42, 'nozzle_temper': 210.5}
    fake_client = _make_fake_client(report_payload=report)

    with patch('bambu_lan.mqtt.Client', return_value=fake_client):
        result = get_bambu_lan_status('192.168.1.50', 'AC12345', 'access123', timeout=2)

    assert result['gcode_state'] == 'RUNNING'
    assert result['mc_percent'] == 42
    assert result['nozzle_temper'] == 210.5
    fake_client.username_pw_set.assert_called_once_with('bblp', 'access123')
    fake_client.subscribe.assert_called_once_with('device/AC12345/report')
    # Requested an immediate full status push rather than waiting on the printer's own cadence
    publish_args = fake_client.publish.call_args
    assert publish_args[0][0] == 'device/AC12345/request'
    assert json.loads(publish_args[0][1])['pushing']['command'] == 'pushall'


def test_get_status_raises_on_auth_failure():
    fake_client = _make_fake_client(connect_reason_code=FakeReasonCode(is_failure=True))

    with patch('bambu_lan.mqtt.Client', return_value=fake_client):
        with pytest.raises(BambuLANError, match='MQTT connect failed'):
            get_bambu_lan_status('192.168.1.50', 'AC12345', 'wrong-code', timeout=2)


def test_get_status_raises_on_no_response():
    fake_client = _make_fake_client(deliver_report=False)

    with patch('bambu_lan.mqtt.Client', return_value=fake_client):
        with pytest.raises(BambuLANError, match='did not respond'):
            get_bambu_lan_status('192.168.1.50', 'AC12345', 'access123', timeout=0.2)


def test_get_status_raises_on_connect_exception():
    fake_client = MagicMock()
    fake_client.connect.side_effect = OSError('No route to host')

    with patch('bambu_lan.mqtt.Client', return_value=fake_client):
        with pytest.raises(BambuLANError, match='Could not connect'):
            get_bambu_lan_status('192.168.1.50', 'AC12345', 'access123', timeout=2)
