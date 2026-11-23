"""Status polling for Klipper printers through Moonraker (Snapmaker U1, Voron, K1, ...).

Moonraker listens on port 7125 by default. With authorization turned on it takes an API key in
the X-Api-Key header. The Snapmaker U1 has four toolheads (extruder .. extruder3); the nozzle
temperature returned is the active toolhead's.
"""
import requests

OBJECTS = ('print_stats', 'virtual_sdcard', 'display_status', 'webhooks', 'toolhead', 'heater_bed',
           'extruder', 'extruder1', 'extruder2', 'extruder3')

# Klipper print_stats.state -> the upper-case states the printer page shows for other printers
STATE_MAP = {
    'standby': 'IDLE',
    'printing': 'PRINTING',
    'paused': 'PAUSED',
    'complete': 'FINISHED',
    'cancelled': 'CANCELLED',
    'error': 'ERROR',
}


class MoonrakerError(Exception):
    """Raised when Moonraker can't be reached or Klipper isn't ready.

    `user_message` is one of the fixed strings below, safe to show the client. Read it through
    that attribute rather than str(e), so no exception text can ever reach an HTTP response.
    """
    def __init__(self, user_message):
        super().__init__(user_message)
        self.user_message = user_message


def parse_status(status: dict) -> dict:
    """Turn a Moonraker printer-objects result into the printer page's status fields."""
    stats = status.get('print_stats') or {}
    webhooks = status.get('webhooks') or {}
    progress = (status.get('display_status') or {}).get('progress') \
        or (status.get('virtual_sdcard') or {}).get('progress') or 0.0

    if webhooks.get('state') in ('shutdown', 'error'):
        state, error = 'ERROR', webhooks.get('state_message') or 'Klipper shut down'
    else:
        state, error = STATE_MAP.get(stats.get('state'), 'UNKNOWN'), stats.get('message') or None
    if state == 'FINISHED':
        progress = 1.0

    active = (status.get('toolhead') or {}).get('extruder') or 'extruder'
    nozzle = (status.get(active) or status.get('extruder') or {}).get('temperature')
    info = stats.get('info') or {}

    return {
        'state': state,
        'job_name': (stats.get('filename') or '').rsplit('/', 1)[-1] or None,
        'progress_percent': round(progress * 100),
        'layers_printed': info.get('current_layer'),
        'total_layers': info.get('total_layer'),
        'nozzle_temp': round(nozzle, 1) if nozzle is not None else None,
        'bed_temp': round((status.get('heater_bed') or {}).get('temperature'), 1)
        if (status.get('heater_bed') or {}).get('temperature') is not None else None,
        'active_tool': active,
        'errors': error,
    }


def get_moonraker_status(api_url: str, api_key: str | None = None, timeout: float = 5) -> dict:
    headers = {'X-Api-Key': api_key} if api_key else {}
    try:
        r = requests.get(f"{api_url.rstrip('/')}/printer/objects/query",
                         params={o: '' for o in OBJECTS}, headers=headers, timeout=timeout)
    except requests.Timeout:
        raise MoonrakerError('Printer did not respond (timeout)')
    except requests.RequestException:
        raise MoonrakerError('Could not reach printer — check the IP and port (Moonraker is usually 7125)')
    if r.status_code in (401, 403):
        raise MoonrakerError('Moonraker rejected the API key')
    if r.status_code == 503:
        raise MoonrakerError('Klipper is not ready (starting up or shut down)')
    if r.status_code != 200:
        raise MoonrakerError(f'Moonraker returned HTTP {r.status_code}')
    try:
        return parse_status(r.json()['result']['status'])
    except (ValueError, KeyError, TypeError):
        raise MoonrakerError('Unexpected response — is this a Moonraker address?')
