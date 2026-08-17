import os
import requests
import pytest
from dotenv import dotenv_values

BASE_URL = dotenv_values('/app/frontend/.env')['REACT_APP_BACKEND_URL'].strip().rstrip('/')

ADMIN_EMAIL = 'test-admin-noc@example.com'
ADMIN_PASS = 'TestAdmin123!'
OPERATOR_EMAIL = 'test-operator-noc@example.com'
OPERATOR_PASS = 'TestOperator123!'


@pytest.fixture(scope='session')
def admin_client():
    s = requests.Session()
    status = s.get(f'{BASE_URL}/api/setup/status').json()
    if status.get('needs_setup'):
        r = s.post(f'{BASE_URL}/api/setup', json={'name': 'TEST Admin', 'email': ADMIN_EMAIL, 'password': ADMIN_PASS})
        assert r.status_code == 200, r.text
    else:
        r = s.post(f'{BASE_URL}/api/auth/login', json={'email': ADMIN_EMAIL, 'password': ADMIN_PASS})
        if r.status_code != 200:
            pytest.skip('Cannot login as test admin; DB has other users. Drop users collection to re-run.')
    return s


@pytest.fixture(scope='session')
def operator_client(admin_client):
    admin_client.post(f'{BASE_URL}/api/users', json={'name': 'TEST Operator', 'email': OPERATOR_EMAIL, 'password': OPERATOR_PASS, 'role': 'operator'})
    s = requests.Session()
    r = s.post(f'{BASE_URL}/api/auth/login', json={'email': OPERATOR_EMAIL, 'password': OPERATOR_PASS})
    assert r.status_code == 200, r.text
    return s


# Setup endpoints
def test_setup_status_after_admin(admin_client):
    r = requests.get(f'{BASE_URL}/api/setup/status')
    assert r.status_code == 200
    assert r.json() == {'needs_setup': False}


def test_setup_conflict_when_users_exist():
    r = requests.post(f'{BASE_URL}/api/setup', json={'name': 'X', 'email': 'x@x.com', 'password': 'password123'})
    assert r.status_code == 409


# Auth flows
def test_login_wrong_password():
    r = requests.post(f'{BASE_URL}/api/auth/login', json={'email': ADMIN_EMAIL, 'password': 'WrongPassword1'})
    assert r.status_code == 401


def test_me_unauthenticated():
    assert requests.get(f'{BASE_URL}/api/auth/me').status_code == 401


def test_login_me_logout_flow(admin_client):
    r = admin_client.get(f'{BASE_URL}/api/auth/me')
    assert r.status_code == 200
    body = r.json()
    assert body['role'] == 'admin'
    assert body['email'] == ADMIN_EMAIL
    # cookie exists
    assert 'access_token' in admin_client.cookies.get_dict()


def test_logout_and_relogin(admin_client):
    s = requests.Session()
    r = s.post(f'{BASE_URL}/api/auth/login', json={'email': ADMIN_EMAIL, 'password': ADMIN_PASS})
    assert r.status_code == 200
    assert s.post(f'{BASE_URL}/api/auth/logout').status_code == 200
    assert s.get(f'{BASE_URL}/api/auth/me').status_code == 401


# Cameras
def test_list_cameras(admin_client):
    r = admin_client.get(f'{BASE_URL}/api/cameras')
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    for cam in data:
        assert '_id' not in cam  # ObjectId must not leak


def test_create_camera_no_objectid_leak(admin_client):
    payload = {'name': 'TEST ObjectId Camera', 'ip': '127.0.0.1', 'nvr': 'TEST-NVR', 'location': 'TEST', 'picture_url': 'http://example.com/p.jpg'}
    r = admin_client.post(f'{BASE_URL}/api/cameras', json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert '_id' not in body, f'ObjectId leak: {body}'
    assert body['name'] == payload['name']
    assert body['ip'] == payload['ip']
    assert body['status'] == 'unknown'
    assert 'id' in body and isinstance(body['id'], str)
    cid = body['id']

    # GET verifies persistence and clean serialization
    listed = admin_client.get(f'{BASE_URL}/api/cameras').json()
    found = next((c for c in listed if c['id'] == cid), None)
    assert found is not None
    assert '_id' not in found

    # Update
    r = admin_client.put(f'{BASE_URL}/api/cameras/{cid}', json={**payload, 'name': 'TEST Updated'})
    assert r.status_code == 200
    assert r.json()['name'] == 'TEST Updated'
    assert '_id' not in r.json()

    # Delete
    r = admin_client.delete(f'{BASE_URL}/api/cameras/{cid}')
    assert r.status_code == 200
    listed = admin_client.get(f'{BASE_URL}/api/cameras').json()
    assert not any(c['id'] == cid for c in listed)


def test_cameras_refresh_updates_history(admin_client):
    before = admin_client.get(f'{BASE_URL}/api/reports/history').json()
    r = admin_client.post(f'{BASE_URL}/api/cameras/refresh', timeout=30)
    assert r.status_code == 200
    items = r.json()
    assert isinstance(items, list)
    for item in items:
        assert item['status'] in ('online', 'offline')
        assert '_id' not in item
    after = admin_client.get(f'{BASE_URL}/api/reports/history').json()
    assert len(after) >= len(before)
    if after:
        latest = after[-1]
        assert {'online', 'offline', 'total', 'availability', 'checked_at'} <= set(latest.keys())


# Ping
def test_ping_offline(admin_client):
    r = admin_client.post(f'{BASE_URL}/api/ping', json={'ip': '127.0.0.1', 'port': 1}, timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body['status'] == 'offline'
    assert body['ip'] == '127.0.0.1'
    assert body.get('target') == '127.0.0.1:1'


def test_ping_no_port_uses_smart_ping_online(admin_client):
    # No port → smart_ping (ICMP → TCP fallback). 8.8.8.8 reachable via TCP:443 in preview.
    r = admin_client.post(f'{BASE_URL}/api/ping', json={'ip': '8.8.8.8'}, timeout=20)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['status'] == 'online', f"expected online, got {body}"
    assert body['ip'] == '8.8.8.8'
    assert body['latency_ms'] is not None and body['latency_ms'] > 0
    assert body.get('target') == '8.8.8.8'  # no :port suffix
    assert body.get('port') in (None, 0) or 'port' in body  # port may be null when not provided


def test_ping_with_port_backward_compat(admin_client):
    # Explicit port=80 still uses TCP ping_host and target has :port suffix
    r = admin_client.post(f'{BASE_URL}/api/ping', json={'ip': '8.8.8.8', 'port': 443}, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['ip'] == '8.8.8.8'
    assert body['port'] == 443
    assert body.get('target') == '8.8.8.8:443'
    assert body['status'] == 'online'
    assert body['latency_ms'] is not None and body['latency_ms'] > 0


# Reports
def test_reports_summary(admin_client):
    r = admin_client.get(f'{BASE_URL}/api/reports/summary')
    assert r.status_code == 200
    body = r.json()
    assert {'total', 'online', 'offline', 'availability'} <= body.keys()
    assert body['total'] == body['online'] + body['offline']


def test_reports_history(admin_client):
    r = admin_client.get(f'{BASE_URL}/api/reports/history')
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_reports_export_csv(admin_client):
    r = admin_client.get(f'{BASE_URL}/api/reports/export')
    assert r.status_code == 200, r.text
    assert 'text/csv' in r.headers.get('content-type', '')
    assert 'name,ip' in r.text


# Operator permissions
def test_operator_can_read_cameras(operator_client):
    assert operator_client.get(f'{BASE_URL}/api/cameras').status_code == 200


def test_operator_cannot_create_camera(operator_client):
    r = operator_client.post(f'{BASE_URL}/api/cameras', json={'name': 'x', 'ip': '127.0.0.1', 'nvr': 'x'})
    assert r.status_code == 403


def test_operator_cannot_delete_camera(admin_client, operator_client):
    # admin creates a camera to attempt delete
    r = admin_client.post(f'{BASE_URL}/api/cameras', json={'name': 'TEST Op-Delete', 'ip': '127.0.0.1', 'nvr': 'x'})
    cid = r.json()['id']
    try:
        assert operator_client.delete(f'{BASE_URL}/api/cameras/{cid}').status_code == 403
    finally:
        admin_client.delete(f'{BASE_URL}/api/cameras/{cid}')


def test_operator_cannot_list_users(operator_client):
    assert operator_client.get(f'{BASE_URL}/api/users').status_code == 403


def test_operator_cannot_create_user(operator_client):
    r = operator_client.post(f'{BASE_URL}/api/users', json={'name': 'x', 'email': 'y@y.com', 'password': 'password123', 'role': 'operator'})
    assert r.status_code == 403


def test_operator_can_ping(operator_client):
    r = operator_client.post(f'{BASE_URL}/api/ping', json={'ip': '127.0.0.1', 'port': 1}, timeout=10)
    assert r.status_code == 200
    assert r.json()['status'] == 'offline'



# ---------- NEW: Settings (Telegram) ----------
def test_settings_get_admin(admin_client):
    r = admin_client.get(f'{BASE_URL}/api/settings')
    assert r.status_code == 200
    b = r.json()
    # Security: raw token must NOT be returned
    assert 'telegram_bot_token' not in b
    for k in ('telegram_bot_token_masked', 'telegram_bot_token_set', 'telegram_chat_id', 'telegram_enabled', 'alert_threshold_minutes'):
        assert k in b
    assert isinstance(b['telegram_enabled'], bool)
    assert isinstance(b['alert_threshold_minutes'], int)
    assert isinstance(b['telegram_bot_token_masked'], str)
    assert isinstance(b['telegram_bot_token_set'], bool)


def test_settings_put_preserves_token_when_empty(admin_client):
    # Set an initial token
    initial = {'telegram_bot_token': '999888:INITIALTOKEN_preserve', 'telegram_chat_id': 'chat-preserve', 'telegram_enabled': False, 'alert_threshold_minutes': 4}
    assert admin_client.put(f'{BASE_URL}/api/settings', json=initial).status_code == 200
    got1 = admin_client.get(f'{BASE_URL}/api/settings').json()
    assert got1['telegram_bot_token_set'] is True
    masked1 = got1['telegram_bot_token_masked']

    # PUT with empty token should preserve existing
    assert admin_client.put(f'{BASE_URL}/api/settings', json={'telegram_bot_token': '', 'telegram_chat_id': 'chat-updated', 'telegram_enabled': True, 'alert_threshold_minutes': 6}).status_code == 200
    got2 = admin_client.get(f'{BASE_URL}/api/settings').json()
    assert got2['telegram_bot_token_set'] is True
    assert got2['telegram_bot_token_masked'] == masked1
    assert got2['telegram_chat_id'] == 'chat-updated'
    assert got2['telegram_enabled'] is True
    assert got2['alert_threshold_minutes'] == 6


def test_settings_put_overwrites_token_when_provided(admin_client):
    admin_client.put(f'{BASE_URL}/api/settings', json={'telegram_bot_token': '111:OLDTOKENOVERWRITE_a', 'telegram_chat_id': 'c', 'telegram_enabled': False, 'alert_threshold_minutes': 5})
    old_masked = admin_client.get(f'{BASE_URL}/api/settings').json()['telegram_bot_token_masked']
    admin_client.put(f'{BASE_URL}/api/settings', json={'telegram_bot_token': '222:NEWTOKENOVERWRITE_b', 'telegram_chat_id': 'c', 'telegram_enabled': False, 'alert_threshold_minutes': 5})
    new_masked = admin_client.get(f'{BASE_URL}/api/settings').json()['telegram_bot_token_masked']
    assert new_masked != old_masked
    assert new_masked.startswith('222:NE')


def test_settings_put_clamps_threshold_min_one(admin_client):
    r = admin_client.put(f'{BASE_URL}/api/settings', json={'telegram_bot_token': '', 'telegram_chat_id': 'c', 'telegram_enabled': False, 'alert_threshold_minutes': 0})
    assert r.status_code == 200
    got = admin_client.get(f'{BASE_URL}/api/settings').json()
    assert got['alert_threshold_minutes'] == 1


def test_settings_operator_forbidden(operator_client):
    assert operator_client.get(f'{BASE_URL}/api/settings').status_code == 403
    assert operator_client.put(f'{BASE_URL}/api/settings', json={'telegram_bot_token': 'x', 'telegram_chat_id': 'y', 'telegram_enabled': False, 'alert_threshold_minutes': 5}).status_code == 403
    assert operator_client.post(f'{BASE_URL}/api/settings/telegram/test', json={}).status_code == 403


def test_settings_put_persists(admin_client):
    payload = {'telegram_bot_token': '123456:TESTFAKETOKEN_abcdef', 'telegram_chat_id': '-1001234567890', 'telegram_enabled': False, 'alert_threshold_minutes': 3}
    r = admin_client.put(f'{BASE_URL}/api/settings', json=payload)
    assert r.status_code == 200
    got = admin_client.get(f'{BASE_URL}/api/settings').json()
    assert got['telegram_chat_id'] == payload['telegram_chat_id']
    assert got['telegram_enabled'] is False
    assert got['alert_threshold_minutes'] == 3
    # masked when >12 chars
    assert '…' in got['telegram_bot_token_masked'] or '***' in got['telegram_bot_token_masked']


def test_telegram_test_endpoint_wired_fake_token(admin_client):
    # ensure token exists so we go past the "belum dikonfigurasi" check → forces real API call → returns 400 error
    admin_client.put(f'{BASE_URL}/api/settings', json={'telegram_bot_token': '111:FAKEBOGUSTOKEN_zzz', 'telegram_chat_id': '999', 'telegram_enabled': False, 'alert_threshold_minutes': 5})
    r = admin_client.post(f'{BASE_URL}/api/settings/telegram/test', json={'message': 'hi'})
    assert r.status_code == 400
    detail = r.json().get('detail', '')
    assert 'Telegram' in detail


def test_telegram_test_endpoint_not_configured(admin_client):
    admin_client.put(f'{BASE_URL}/api/settings', json={'telegram_bot_token': '', 'telegram_chat_id': '', 'telegram_enabled': False, 'alert_threshold_minutes': 5})
    r = admin_client.post(f'{BASE_URL}/api/settings/telegram/test', json={})
    assert r.status_code == 400
    assert 'belum dikonfigurasi' in r.json().get('detail', '')


# ---------- NEW: Camera import CSV ----------
def test_import_cameras_valid_csv(admin_client):
    csv_bytes = b"name,ip,nvr,location,picture_url\nTEST_IMP_CAM1,10.99.0.1,NVR-TEST,TEST Site,http://ex/p1.jpg\nTEST_IMP_CAM2,10.99.0.2,NVR-TEST,TEST Site,http://ex/p2.jpg\n"
    r = admin_client.post(f'{BASE_URL}/api/cameras/import', files={'file': ('cams.csv', csv_bytes, 'text/csv')})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['inserted'] == 2
    assert body['skipped'] == 0
    # cleanup
    cams = admin_client.get(f'{BASE_URL}/api/cameras').json()
    for c in cams:
        if c['name'].startswith('TEST_IMP_CAM'):
            admin_client.delete(f'{BASE_URL}/api/cameras/{c["id"]}')


def test_import_cameras_partial_skip(admin_client):
    csv_bytes = b"name,ip,nvr,location,picture_url\nTEST_IMP_OK,10.99.1.1,NVR,Site,\n,10.99.1.2,NVR,Site,\nMissingIP,,NVR,Site,\n"
    r = admin_client.post(f'{BASE_URL}/api/cameras/import', files={'file': ('cams.csv', csv_bytes, 'text/csv')})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['inserted'] == 1
    assert body['skipped'] == 2
    assert len(body['errors']) >= 2
    # cleanup
    cams = admin_client.get(f'{BASE_URL}/api/cameras').json()
    for c in cams:
        if c['name'] == 'TEST_IMP_OK':
            admin_client.delete(f'{BASE_URL}/api/cameras/{c["id"]}')


def test_import_cameras_operator_forbidden(operator_client):
    csv_bytes = b"name,ip,nvr,location,picture_url\nx,1.1.1.1,NVR,Site,\n"
    r = operator_client.post(f'{BASE_URL}/api/cameras/import', files={'file': ('cams.csv', csv_bytes, 'text/csv')})
    assert r.status_code == 403


# ---------- NEW: Camera uptime report ----------
def test_camera_uptime_after_refresh(admin_client):
    # Ensure at least one refresh has run so camera_daily has today's data
    r = admin_client.post(f'{BASE_URL}/api/cameras/refresh', timeout=30)
    assert r.status_code == 200
    r = admin_client.get(f'{BASE_URL}/api/reports/camera-uptime?days=7')
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) > 0, 'expected at least one camera in uptime report'
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    for entry in data:
        assert 'camera_id' in entry and 'camera_name' in entry and 'days' in entry
        assert isinstance(entry['days'], list)
        days_map = {d['day']: d for d in entry['days']}
        assert today in days_map, f"today's day missing for {entry['camera_name']}"
        d = days_map[today]
        assert d['total'] >= 1
        assert 0 <= d['uptime'] <= 100
        assert '_id' not in entry


def test_camera_daily_counter_increments_on_two_refreshes(admin_client):
    # baseline
    r0 = admin_client.get(f'{BASE_URL}/api/reports/camera-uptime?days=1').json()
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    base = {}
    for e in r0:
        for d in e['days']:
            if d['day'] == today:
                base[e['camera_id']] = d['total']
    # two refreshes
    assert admin_client.post(f'{BASE_URL}/api/cameras/refresh', timeout=30).status_code == 200
    assert admin_client.post(f'{BASE_URL}/api/cameras/refresh', timeout=30).status_code == 200
    r1 = admin_client.get(f'{BASE_URL}/api/reports/camera-uptime?days=1').json()
    for e in r1:
        for d in e['days']:
            if d['day'] == today:
                b = base.get(e['camera_id'], 0)
                assert d['total'] >= b + 2, f"expected counter to grow by >=2 for {e['camera_name']}, base={b} now={d['total']}"


# ---------- NEW: Refresh must not crash when Telegram enabled with bogus token ----------
def test_refresh_survives_bogus_telegram(admin_client):
    # Enable Telegram with fake token so send_telegram will 400, but process_alerts should swallow it
    admin_client.put(f'{BASE_URL}/api/settings', json={'telegram_bot_token': '111:FAKEBOGUS', 'telegram_chat_id': '999', 'telegram_enabled': True, 'alert_threshold_minutes': 0})
    r = admin_client.post(f'{BASE_URL}/api/cameras/refresh', timeout=30)
    assert r.status_code == 200
    # cleanup: disable
    admin_client.put(f'{BASE_URL}/api/settings', json={'telegram_bot_token': '', 'telegram_chat_id': '', 'telegram_enabled': False, 'alert_threshold_minutes': 5})
