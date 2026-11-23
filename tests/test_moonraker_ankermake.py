"""Status for Klipper/Moonraker printers (e.g. Snapmaker U1) and AnkerMake printers."""
from unittest.mock import MagicMock, patch

import requests

from models import db, Printer, PrinterConnection, PrinterLiveStatus
from moonraker_api import parse_status


def _connection(user, connection_type, api_url='http://192.168.1.70:7125', api_key=None):
    printer = Printer(user_id=user.id, name=f'Test {connection_type}', status='IDLE')
    db.session.add(printer)
    db.session.flush()
    conn = PrinterConnection(printer_id=printer.id, user_id=user.id, connection_type=connection_type,
                             api_url=api_url, api_key=api_key)
    db.session.add(conn)
    db.session.commit()
    return conn


U1_PRINTING = {
    'print_stats': {'state': 'printing', 'filename': 'gifts/dragon.gcode', 'message': '',
                    'info': {'current_layer': 42, 'total_layer': 300}},
    'virtual_sdcard': {'progress': 0.30}, 'display_status': {'progress': 0.34},
    'webhooks': {'state': 'ready'}, 'toolhead': {'extruder': 'extruder2'},
    'extruder': {'temperature': 30.0}, 'extruder2': {'temperature': 219.96},
    'heater_bed': {'temperature': 65.04},
}


def test_parse_u1_uses_active_toolhead_and_slicer_progress():
    s = parse_status(U1_PRINTING)
    assert s['state'] == 'PRINTING'
    assert s['nozzle_temp'] == 220.0 and s['active_tool'] == 'extruder2'
    assert s['bed_temp'] == 65.0
    assert s['progress_percent'] == 34
    assert (s['layers_printed'], s['total_layers']) == (42, 300)
    assert s['job_name'] == 'dragon.gcode'


def test_parse_klipper_shutdown_is_error():
    s = parse_status({'webhooks': {'state': 'shutdown', 'state_message': 'MCU timeout'},
                      'print_stats': {'state': 'printing'}})
    assert s['state'] == 'ERROR' and s['errors'] == 'MCU timeout'


def test_moonraker_status_endpoint(client, user, token):
    conn = _connection(user, 'moonraker', api_key='abc')
    resp_obj = MagicMock(status_code=200)
    resp_obj.json.return_value = {'result': {'status': U1_PRINTING}}
    with patch('moonraker_api.requests.get', return_value=resp_obj) as get:
        resp = client.get(f'/api/printer-connections/{conn.id}/status',
                          headers={'Authorization': f'Bearer {token}'})
    assert resp.status_code == 200
    assert resp.get_json()['status']['state'] == 'PRINTING'
    assert get.call_args.args[0] == 'http://192.168.1.70:7125/printer/objects/query'
    assert get.call_args.kwargs['headers'] == {'X-Api-Key': 'abc'}
    assert db.session.get(PrinterConnection, conn.id).status == 'connected'


def test_moonraker_unreachable_returns_502(client, user, token):
    conn = _connection(user, 'klipper')
    with patch('moonraker_api.requests.get', side_effect=requests.ConnectionError()):
        resp = client.get(f'/api/printer-connections/{conn.id}/status',
                          headers={'Authorization': f'Bearer {token}'})
    assert resp.status_code == 502
    assert db.session.get(PrinterConnection, conn.id).status == 'error'


def test_create_ankermake_printer_without_url(client, user, token):
    resp = client.post('/api/printers', json={'name': 'AnkerMake M5', 'model': 'M5',
                                              'connection_type': 'ankermake'},
                       headers={'Authorization': f'Bearer {token}'})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body['connection_type'] == 'ankermake'
    assert body['api_url'] == ''   # not defaulted to Bambu's cloud


def test_ankermake_status_without_printhub_explains(client, user, token):
    conn = _connection(user, 'ankermake', api_url='')
    resp = client.get(f'/api/printer-connections/{conn.id}/status',
                      headers={'Authorization': f'Bearer {token}'})
    assert resp.status_code == 409
    assert 'PrintHub' in resp.get_json()['error']


def test_ankermake_status_from_live_status(client, user, token):
    conn = _connection(user, 'ankermake', api_url='')
    db.session.add(PrinterLiveStatus(user_id=user.id, printer_id=conn.printer_id, source='PrintHub',
                                     state='printing', progress=41.6, nozzle_temp=210.0, bed_temp=60.0))
    db.session.commit()
    resp = client.get(f'/api/printer-connections/{conn.id}/status',
                      headers={'Authorization': f'Bearer {token}'})
    assert resp.status_code == 200
    assert resp.get_json()['status'] == {'state': 'PRINTING', 'progress_percent': 42, 'nozzle_temp': 210.0,
                                         'bed_temp': 60.0, 'errors': None}


def test_update_rejects_unknown_connection_type(client, user, token):
    conn = _connection(user, 'moonraker')
    resp = client.put(f'/api/printers/{conn.printer_id}', json={'connection_type': 'bogus'},
                      headers={'Authorization': f'Bearer {token}'})
    assert resp.status_code == 400
